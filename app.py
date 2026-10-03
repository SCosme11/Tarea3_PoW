"""Aplicación Flask del simulador de Proof of Work.

Rutas
-----
GET  /             formulario, transacción pendiente, carrera de nodos y cadena (con su validez)
POST /transaccion  arma la transacción, la firma y la deja pendiente
POST /minar        valida (firma y reglas) y lanza un hilo por nodo
GET  /estado       JSON con el avance de la carrera (lo consulta la página cada 300 ms)
POST /alterar      (extra, demostración) altera un bloque ya minado sin recalcular
POST /restaurar    (extra, demostración) deshace la alteración

Se ejecuta con app.run(debug=True, use_reloader=False): el recargador lanzaría un
segundo proceso y duplicaría el estado global (cadena, nodos e hilos).
"""
import os
from collections import defaultdict

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

from blockchain import DIFICULTAD, NUM_NODOS, RECOMPENSA, Simulador, hash_bloque

ETIQUETAS = {
    "hash": "El hash no coincide",
    "firma": "Firma inválida",
    "dificultad": "Sin prueba de trabajo",
    "encadenamiento": "Enlace roto",
    "regla": "Regla de moneda",
    "orden": "Posición incorrecta",
    "génesis": "Génesis alterado",
}


def cuadricula(h):
    """Huella visual de un hash: matriz 5x5 simétrica (lista de 25 booleanos, por filas).

    Se toman 15 bits del hash (3 columnas x 5 filas) y se reflejan. Es determinista:
    el mismo hash siempre dibuja la misma figura, y un hash distinto casi siempre otra.
    """
    try:
        bits = int(h[:4], 16) & 0x7FFF
    except (ValueError, TypeError):
        bits = 0
    celdas = []
    for fila in range(5):
        izq = [bool(bits >> (fila * 3 + c) & 1) for c in range(3)]
        celdas += izq + izq[1::-1]            # columnas 0,1,2,1,0
    return celdas


def ceros(h):
    return len(h) - len(h.lstrip("0"))


def partir(h, largo):
    """Parte un hash en (ceros iniciales, resto) recortado a `largo` caracteres."""
    visible = h[:largo]
    z = ceros(visible)
    return visible[:z], visible[z:]


def crear_app(dificultad=None, sim=None):
    app = Flask(__name__)
    app.secret_key = os.environ.get("POW_SECRET", "clave-solo-para-desarrollo-local")
    if sim is None:
        d = dificultad if dificultad is not None else int(os.environ.get("POW_DIFICULTAD", DIFICULTAD))
        sim = Simulador(dificultad=d)
    app.config["SIM"] = sim

    def vista_bloques(errores):
        por_bloque = defaultdict(list)
        for e in errores:
            por_bloque[e["bloque"]].append(e["regla"])
        bl = list(sim.cadena.bloques)
        vistas = []
        for j, b in enumerate(bl):
            tx = b["transaccion"]
            c = tx["contenido"]
            guardado = b.get("hash", "")
            recalculado = hash_bloque(b)
            if tx["proposito"] == "moneda":
                resumen = {"de": sim.nombre_de(tx["remitente"]), "para": sim.nombre_de(c["para"]),
                           "monto": c["monto"], "secuencia": c["secuencia"]}
            else:
                resumen = None
            fallos = []
            for regla in por_bloque[j]:
                et = ETIQUETAS.get(regla, regla)
                if et not in fallos:
                    fallos.append(et)
            z_h, r_h = partir(guardado, 20)
            z_a, r_a = partir(b["hash_anterior"], 10)
            vistas.append({
                "numero": b["numero"], "nonce": b["nonce"], "resumen": resumen,
                "minero": sim.nombre_de(b["minero"]), "genesis": j == 0,
                "hash_z": z_h, "hash_r": r_h, "ant_z": z_a, "ant_r": r_a, "hash": guardado,
                "huella": cuadricula(recalculado),                       # huella del contenido ACTUAL
                "huella_ant": cuadricula(b["hash_anterior"]),           # lo que este bloque dice que lo precede
                "ok": not fallos, "fallos": fallos,
                "recalculado": recalculado[:20] + "…" if recalculado != guardado else None,
                "enlace_ok": j == 0 or b["hash_anterior"] == bl[j - 1].get("hash"),
                "ganador": j == len(bl) - 1 and j > 0 and sim.estado["ganador"] == sim.nombre_de(b["minero"]),
            })
        return vistas

    @app.get("/")
    def index():
        c = sim.cadena
        saldos, _ = c.estado()
        errores = c.errores()
        pendiente = None
        if c.pendiente:
            tx = c.pendiente["transaccion"]
            pendiente = {
                "remitente": sim.nombre_de(tx["remitente"]),
                "para": sim.nombre_de(tx["contenido"]["para"]),
                "monto": tx["contenido"]["monto"],
                "secuencia": tx["contenido"]["secuencia"],
                "firma": c.pendiente["firma"][:24] + "…",
                "firma_ok": c.errores_transaccion(tx, c.pendiente["firma"]) == [],
            }
        return render_template(
            "index.html",
            usuarios=[{"nombre": n, "pub": w.pub[:12] + "…", "saldo": saldos[w.pub]} for n, w in sim.usuarios.items()],
            nodos=sim.resumen()["nodos"],
            bloques=vista_bloques(errores),
            valida=not errores, errores=errores, pendiente=pendiente,
            estado=sim.estado, dificultad=c.dificultad, recompensa=RECOMPENSA,
            n_nodos=NUM_NODOS, alterada=sim._respaldo is not None,
            modos=sim.MODOS_ALTERACION,
        )

    @app.post("/transaccion")
    def transaccion():
        try:
            monto = int(request.form["monto"])
            sim.crear_transaccion(
                request.form["remitente"], request.form["para"], monto,
                alterar_despues=request.form.get("alterar") == "on",
            )
            flash("Transacción firmada. Ya puedes minar.", "ok")
        except (ValueError, KeyError):
            flash("Datos inválidos: el monto debe ser un entero.", "error")
        except RuntimeError as e:
            flash(str(e), "error")
        return redirect(url_for("index"))

    @app.post("/minar")
    def minar():
        motivos = sim.iniciar_minado()
        if motivos:
            flash("Transacción rechazada: " + "; ".join(motivos), "error")
        return redirect(url_for("index"))

    @app.get("/estado")
    def estado():
        return jsonify(sim.resumen())

    @app.post("/alterar")
    def alterar():
        try:
            sim.alterar(int(request.form["numero"]), request.form["modo"])
            flash("Bloque alterado sin recalcular nada. Mira cómo cambia su huella.", "ok")
        except (ValueError, KeyError, RuntimeError) as e:
            flash(str(e), "error")
        return redirect(url_for("index"))

    @app.post("/restaurar")
    def restaurar():
        sim.restaurar()
        flash("Cadena restaurada.", "ok")
        return redirect(url_for("index"))

    return app


app = crear_app()

if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)
