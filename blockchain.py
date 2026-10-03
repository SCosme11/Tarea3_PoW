"""Simulador de Proof of Work: núcleo del blockchain.

Contenido
---------
Etapa 1  sha256 / hash_bloque, constantes DIFICULTAD y RECOMPENSA
Etapa 2  Billetera (Ed25519) y verificar_firma
Etapa 3  Nodo (minero en un hilo) con reparto de nonces nonce_{i,k} = i + k*n
Etapa 4  Cadena y su validación (es_valida / errores)
Extra    Simulador: orquesta billeteras, nodos, carrera y demostraciones de alteración

Propósito elegido: MONEDA CON SALDO. Reglas de negocio validadas antes de minar
(y de nuevo al validar toda la cadena): saldo suficiente, secuencia correlativa por
remitente (anti-repetición) y monto entero positivo.
"""
import copy
import hashlib
import json
import random
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization as ser
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

# --------------------------------------------------------------------------- #
# Constantes
# --------------------------------------------------------------------------- #
DIFICULTAD = 5          # ceros hexadecimales al inicio del hash (p = 16**-5 por intento)
RECOMPENSA = 50         # monedas que cobra el nodo ganador
NUM_NODOS = 4           # nodos mineros (uno por hilo)
SALDO_INICIAL = 100     # asignación del bloque génesis a cada usuario
HASH_NULO = "0" * 64    # hash_anterior del génesis

# Candado único para la sección crítica "agregar bloque + cobrar + avisar".
candado = threading.Lock()


# --------------------------------------------------------------------------- #
# Etapa 1. Hash del bloque
# --------------------------------------------------------------------------- #
def sha256(d):
    """SHA-256 de un diccionario, con serialización determinista (llaves ordenadas)."""
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()


def hash_bloque(b):
    """Hash de un bloque. El campo "hash" no entra en el cálculo."""
    return sha256({k: v for k, v in b.items() if k != "hash"})


def cumple_dificultad(h, dificultad):
    """True si el hash hexadecimal empieza con `dificultad` ceros."""
    return h.startswith("0" * dificultad)


# --------------------------------------------------------------------------- #
# Etapa 2. Billeteras y firma digital (Ed25519)
# --------------------------------------------------------------------------- #
class Billetera:
    """Par de llaves Ed25519. La llave pública (hex, 64 caracteres) es la identidad."""

    def __init__(self, nombre):
        self.nombre = nombre
        self._priv = Ed25519PrivateKey.generate()
        self.pub = (
            self._priv.public_key()
            .public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)
            .hex()
        )

    def firmar(self, tx):
        """Firma (hex, 128 caracteres) del JSON canónico de la transacción."""
        return self._priv.sign(json.dumps(tx, sort_keys=True).encode()).hex()


def verificar_firma(tx, firma):
    """True/False. Toma la llave pública de tx["remitente"]; nunca lanza excepción."""
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(tx["remitente"]))
        pub.verify(bytes.fromhex(firma), json.dumps(tx, sort_keys=True).encode())
        return True
    except (InvalidSignature, ValueError, KeyError, TypeError):
        return False


def crear_transaccion(remitente, para, monto, secuencia, hora=None):
    """Arma una transacción del propósito "moneda" (sin firmar)."""
    return {
        "proposito": "moneda",
        "remitente": remitente,
        "contenido": {"para": para, "monto": monto, "secuencia": secuencia},
        "hora": hora or datetime.now(timezone.utc).isoformat(timespec="microseconds"),
    }


# --------------------------------------------------------------------------- #
# Etapa 4. Bloque génesis y cadena
# --------------------------------------------------------------------------- #
def bloque_genesis(asignaciones):
    """Bloque 0. No requiere minería ni firma; reparte los saldos iniciales."""
    b = {
        "numero": 0,
        "nonce": 0,
        "transaccion": {
            "proposito": "genesis",
            "remitente": HASH_NULO,
            "contenido": {"asignaciones": dict(asignaciones)},
            "hora": "2026-01-01T00:00:00+00:00",
        },
        "firma": "",
        "hash_anterior": HASH_NULO,
        "minero": "genesis",
    }
    b["hash"] = hash_bloque(b)
    return b


def _es_hex_llave(s):
    if not isinstance(s, str) or len(s) != 64:
        return False
    try:
        bytes.fromhex(s)
        return True
    except ValueError:
        return False


class Cadena:
    """Lista de bloques (empieza con el génesis) y la transacción pendiente."""

    def __init__(self, dificultad=DIFICULTAD, asignaciones=None):
        self.dificultad = dificultad
        self.bloques = [bloque_genesis(asignaciones or {})]
        self.pendiente = None  # {"transaccion": dict, "firma": hex}

    # ---- estado derivado de la cadena (fuente de verdad de los saldos) ---- #
    @staticmethod
    def _aplicar(saldos, secuencias, b):
        tx = b["transaccion"]
        c = tx["contenido"]
        saldos[tx["remitente"]] -= c["monto"]
        saldos[c["para"]] += c["monto"]
        saldos[b["minero"]] += RECOMPENSA
        secuencias[tx["remitente"]] = c["secuencia"]

    @staticmethod
    def _inicial(genesis):
        saldos, secuencias = defaultdict(int), defaultdict(int)
        try:
            for k, v in genesis["transaccion"]["contenido"]["asignaciones"].items():
                saldos[k] += v
        except (KeyError, TypeError, AttributeError):
            pass
        return saldos, secuencias

    def estado(self):
        """Reproduce la cadena y devuelve (saldos, última secuencia por remitente)."""
        bloques = list(self.bloques)
        saldos, secuencias = self._inicial(bloques[0])
        for b in bloques[1:]:
            try:
                self._aplicar(saldos, secuencias, b)
            except (KeyError, TypeError):
                continue  # bloque alterado y mal formado: lo reportará es_valida
        return saldos, secuencias

    def saldo(self, clave):
        return self.estado()[0][clave]

    # ---- reglas del propósito "moneda" ---- #
    @staticmethod
    def _reglas(tx, saldos, secuencias):
        """Reglas de negocio (sin la firma). Devuelve la lista de motivos de rechazo."""
        try:
            if tx["proposito"] != "moneda":
                return ["el propósito de la transacción no es 'moneda'"]
            rem = tx["remitente"]
            c = tx["contenido"]
            para, monto, sec = c["para"], c["monto"], c["secuencia"]
        except (KeyError, TypeError):
            return ["transacción mal formada"]
        motivos = []
        if not _es_hex_llave(rem) or not _es_hex_llave(para):
            return ["remitente o destinatario no es una llave pública válida"]
        if type(monto) is not int or monto <= 0:
            motivos.append("el monto debe ser un entero positivo")
        if type(sec) is not int:
            motivos.append("la secuencia debe ser un entero")
        if para == rem:
            motivos.append("el remitente no puede enviarse monedas a sí mismo")
        if motivos:
            return motivos
        if saldos[rem] < monto:
            motivos.append(f"saldo insuficiente (tiene {saldos[rem]}, envía {monto})")
        if sec != secuencias[rem] + 1:
            motivos.append(
                f"secuencia {sec} inválida (se esperaba {secuencias[rem] + 1}): "
                "transacción repetida o fuera de orden"
            )
        return motivos

    def errores_transaccion(self, tx, firma):
        """Motivos por los que una transacción NO puede minarse ahora ([] = válida)."""
        motivos = []
        if not verificar_firma(tx, firma):
            motivos.append("firma digital inválida")
        saldos, secuencias = self.estado()
        motivos += self._reglas(tx, saldos, secuencias)
        return motivos

    # ---- creación del bloque a minar ---- #
    def nuevo_bloque(self, minero):
        """Bloque candidato con la transacción pendiente (cada nodo usa su minero)."""
        p = self.pendiente
        return {
            "numero": len(self.bloques),
            "nonce": 0,
            "transaccion": copy.deepcopy(p["transaccion"]),
            "firma": p["firma"],
            "hash_anterior": self.bloques[-1]["hash"],
            "minero": minero,
        }

    # ---- Etapa 4. Validación ---- #
    def errores(self):
        """Lista de {bloque, regla, detalle} con todo lo que está mal en la cadena."""
        errs = []

        def err(numero, regla, detalle):
            errs.append({"bloque": numero, "regla": regla, "detalle": detalle})

        bl = list(self.bloques)
        g = bl[0]
        if g.get("hash_anterior") != HASH_NULO:
            err(0, "génesis", "hash_anterior distinto de 64 ceros")
        if hash_bloque(g) != g.get("hash"):
            err(0, "hash", "el hash guardado del génesis no coincide con el recalculado")

        saldos, secuencias = self._inicial(g)
        for j in range(1, len(bl)):
            prev, b = bl[j - 1], bl[j]
            if b.get("numero") != j:
                err(j, "orden", f"numero={b.get('numero')} pero ocupa la posición {j}")
            # (a) encadenamiento
            if b.get("hash_anterior") != prev.get("hash"):
                err(j, "encadenamiento", "hash_anterior no coincide con el hash del bloque previo")
            # (b) hash guardado = hash recalculado
            if hash_bloque(b) != b.get("hash"):
                err(j, "hash", "el hash guardado no coincide con el recalculado (bloque alterado)")
            # (c) prueba de trabajo
            if not cumple_dificultad(str(b.get("hash")), self.dificultad):
                err(j, "dificultad", f"el hash no empieza con {self.dificultad} ceros (sin prueba de trabajo)")
            # (d) firma de la transacción
            if not verificar_firma(b.get("transaccion", {}), b.get("firma", "")):
                err(j, "firma", "la firma Ed25519 de la transacción no es válida")
            # (e) reglas del propósito, reproduciendo el estado en ese punto
            try:
                for m in self._reglas(b["transaccion"], saldos, secuencias):
                    err(j, "regla", m)
                self._aplicar(saldos, secuencias, b)
            except (KeyError, TypeError):
                err(j, "regla", "bloque o transacción mal formados")
        return errs

    def es_valida(self):
        return not self.errores()


# --------------------------------------------------------------------------- #
# Etapa 3. Nodos mineros
# --------------------------------------------------------------------------- #
def nonces_nodo(i, n, cuantos):
    """Primeros `cuantos` nonces que prueba el nodo i de n: i, i+n, i+2n, ..."""
    return [i + k * n for k in range(cuantos)]


class Nodo:
    """Minero. El nodo i (0 <= i < n) prueba nonce_{i,k} = i + k*n."""

    def __init__(self, i, n, nombre, billetera=None):
        self.i = i
        self.n = n
        self.nombre = nombre
        self.billetera = billetera or Billetera(nombre)
        self.intentos = 0
        self.ultimo = ""
        self.mejor = 0        # máximo de ceros iniciales alcanzado en la carrera (solo para la interfaz)
        self.ganados = 0

    @property
    def pub(self):
        return self.billetera.pub

    def reiniciar(self):
        self.intentos = 0
        self.ultimo = ""
        self.mejor = 0

    def minar(self, bloque, cadena, fin, estado):
        prefijo = "0" * cadena.dificultad
        bloque["nonce"] = self.i                          # k = 0: nonce = i
        while not fin.is_set():
            h = hash_bloque(bloque)
            self.intentos += 1
            self.ultimo = h
            if h[0] == "0":                               # solo 1 de cada 16 hashes entra aquí
                ceros = len(h) - len(h.lstrip("0"))
                if ceros > self.mejor:
                    self.mejor = ceros
            if h.startswith(prefijo):
                with candado:
                    if fin.is_set():                      # alguien ganó antes
                        return
                    if bloque["hash_anterior"] != cadena.bloques[-1]["hash"]:
                        return                            # la cadena cambió: bloque obsoleto
                    bloque["hash"] = h
                    cadena.bloques.append(bloque)
                    self.ganados += 1                     # el saldo se deriva de la cadena
                    cadena.pendiente = None
                    estado["ganador"] = self.nombre
                    estado["nonce_ganador"] = bloque["nonce"]
                    estado["duracion"] = time.perf_counter() - estado["inicio"]
                    fin.set()                             # todos se detienen
                return
            bloque["nonce"] += self.n                     # i + k*n -> i + (k+1)*n


# --------------------------------------------------------------------------- #
# Orquestación: usuarios, nodos, carrera y demostraciones de alteración
# --------------------------------------------------------------------------- #
class Simulador:
    def __init__(
        self,
        dificultad=DIFICULTAD,
        n_nodos=NUM_NODOS,
        usuarios=("Alice", "Beto", "Carla"),
        saldo_inicial=SALDO_INICIAL,
    ):
        self.usuarios = {u: Billetera(u) for u in usuarios}
        self.nodos = [Nodo(i, n_nodos, f"Nodo {i}") for i in range(n_nodos)]
        asignaciones = {w.pub: saldo_inicial for w in self.usuarios.values()}
        self.cadena = Cadena(dificultad, asignaciones)
        self.nombres = {w.pub: u for u, w in self.usuarios.items()}
        self.nombres.update({nd.pub: nd.nombre for nd in self.nodos})
        self.fin = threading.Event()
        self.estado = {
            "minando": False, "ganador": None, "duracion": None,
            "inicio": None, "intentos_total": 0, "nonce_ganador": None,
        }
        self._cerrojo = threading.Lock()   # evita lanzar dos carreras a la vez
        self._respaldo = None
        self._supervisor = None

    def nombre_de(self, clave):
        return self.nombres.get(clave, clave if len(clave) <= 12 else clave[:8] + "…")

    # ---- transacciones ---- #
    def crear_transaccion(self, remitente, para, monto, alterar_despues=False):
        """Arma, firma y deja pendiente la transacción. Con `alterar_despues` cambia el
        monto DESPUÉS de firmar (para demostrar que una firma inválida se rechaza)."""
        if self.estado["minando"]:
            raise RuntimeError("hay una carrera de minería en curso")
        w = self.usuarios[remitente]
        _, secuencias = self.cadena.estado()
        tx = crear_transaccion(w.pub, self.usuarios[para].pub, monto, secuencias[w.pub] + 1)
        firma = w.firmar(tx)
        if alterar_despues:
            tx["contenido"]["monto"] += 1
        self.cadena.pendiente = {"transaccion": tx, "firma": firma}
        return tx

    # ---- carrera de minería ---- #
    def iniciar_minado(self):
        """Valida la transacción pendiente y lanza un hilo por nodo (no bloquea).
        Devuelve la lista de motivos de rechazo; [] si la carrera comenzó."""
        with self._cerrojo:
            if self.estado["minando"]:
                return ["ya hay una carrera en curso"]
            p = self.cadena.pendiente
            if p is None:
                return ["no hay transacción pendiente"]
            motivos = self.cadena.errores_transaccion(p["transaccion"], p["firma"])
            if motivos:
                self.cadena.pendiente = None      # se descarta la transacción rechazada
                return motivos
            self.fin = threading.Event()
            self.estado.update(
                minando=True, ganador=None, duracion=None, nonce_ganador=None,
                intentos_total=0, inicio=time.perf_counter(),
            )
            hilos = []
            for nodo in self.nodos:
                nodo.reiniciar()
                bloque = self.cadena.nuevo_bloque(nodo.pub)    # mismo contenido, distinto minero
                hilos.append(threading.Thread(
                    target=nodo.minar, args=(bloque, self.cadena, self.fin, self.estado),
                    daemon=True, name=nodo.nombre))
            self._supervisor = threading.Thread(target=self._supervisar, args=(hilos,), daemon=True)
            # Orden de arranque aleatorio: con el GIL, el primer hilo iniciado corre hasta ~5 ms
            # (sys.getswitchinterval) antes de ceder; sin sorteo, el nodo 0 tendría ventaja sistemática.
            for h in random.sample(hilos, len(hilos)):
                h.start()
            self._supervisor.start()   # después de arrancar a todos: join() exige hilos ya iniciados
            return []

    def _supervisar(self, hilos):
        self.fin.wait()                      # espera al ganador
        for h in hilos:
            h.join()                         # y a que todos los hilos terminen
        self.estado["intentos_total"] = sum(n.intentos for n in self.nodos)
        self.estado["minando"] = False

    def minar_y_esperar(self):
        """Versión bloqueante (pruebas y benchmark). Lanza ValueError si se rechaza."""
        motivos = self.iniciar_minado()
        if motivos:
            raise ValueError("; ".join(motivos))
        self._supervisor.join()
        return dict(self.estado)

    # ---- demostraciones de alteración ---- #
    MODOS_ALTERACION = ("contenido", "contenido_y_hash", "hash", "firma", "minero")

    def alterar(self, numero, modo):
        if self.estado["minando"]:
            raise RuntimeError("hay una carrera de minería en curso")
        if not 1 <= numero < len(self.cadena.bloques):
            raise ValueError("ese bloque no existe (el génesis no se altera)")
        if modo not in self.MODOS_ALTERACION:
            raise ValueError("modo de alteración desconocido")
        if self._respaldo is None:
            self._respaldo = copy.deepcopy(self.cadena.bloques)
        b = self.cadena.bloques[numero]
        if modo in ("contenido", "contenido_y_hash"):
            b["transaccion"]["contenido"]["monto"] += 1
            if modo == "contenido_y_hash":
                b["hash"] = hash_bloque(b)           # el atacante recalcula el hash...
        elif modo == "hash":
            b["hash"] = "0" * self.cadena.dificultad + "f" * (64 - self.cadena.dificultad)
        elif modo == "firma":
            b["firma"] = next(iter(self.usuarios.values())).firmar({"otra": "cosa"})
        elif modo == "minero":
            b["minero"] = next(iter(self.usuarios.values())).pub

    def restaurar(self):
        if self._respaldo is not None:
            self.cadena.bloques[:] = self._respaldo
            self._respaldo = None

    # ---- datos para la interfaz ---- #
    def resumen(self):
        saldos, _ = self.cadena.estado()
        return {
            "minando": self.estado["minando"],
            "ganador": self.estado["ganador"],
            "duracion": self.estado["duracion"],
            "intentos_total": self.estado["intentos_total"],
            "longitud": len(self.cadena.bloques),
            "nodos": [
                {"i": n.i, "nombre": n.nombre, "intentos": n.intentos, "ultimo": n.ultimo,
                 "mejor": n.mejor, "saldo": saldos[n.pub], "ganados": n.ganados}
                for n in self.nodos
            ],
        }
