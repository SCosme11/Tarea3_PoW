# Simulador de Proof of Work con Flask

Aplicación web en Python en la que **cuatro nodos mineros compiten en hilos** por minar un bloque que contiene una **transacción firmada con Ed25519**. El primer nodo que encuentra un nonce válido detiene a los demás, agrega el bloque a la cadena y cobra la recompensa. No hay red P2P: todos los nodos viven en el mismo proceso.

Proyecto de la materia *Fundamentos de Blockchain* (Universidad Anáhuac México). El propósito de la cadena es **moneda con saldo**: cada bloque registra una transferencia entre billeteras y el sistema valida saldo suficiente y una secuencia anti-repetición antes de minar.


## Qué incluye

- Bloque con `numero`, `nonce`, `transaccion`, `firma`, `hash_anterior`, `minero` y `hash` (SHA-256 sobre JSON con llaves ordenadas).
- Billeteras y firma digital **Ed25519** (biblioteca `cryptography`); una firma inválida se rechaza antes de minar.
- Minería concurrente: 4 hilos, reparto de nonces `i + k·n`, `threading.Event` para detener a todos y `threading.Lock` para que nunca se agreguen dos bloques.
- Recompensa de 50 monedas **sellada dentro del hash** (campo `minero`); los saldos se recalculan reproduciendo la cadena.
- Validación completa de la cadena: encadenamiento, hash, prueba de trabajo, firma y reglas de saldo/secuencia.
- Interfaz en vivo: se ve a cada nodo probar hashes (consulta a `/estado` cada 300 ms), al ganador con trofeo, una huella de colores por bloque (cambia si el bloque se altera) y la validez de la cadena.
- Herramienta de demostración para **alterar un bloque ya minado** y ver cómo la cadena se reporta inválida.
- 44 pruebas automáticas y un script de benchmark para la tabla de dificultad.

## Estructura del repositorio

```
.
├── blockchain.py          # núcleo: hash, billeteras, firma, nodos, cadena, simulador
├── app.py                 # aplicación Flask (rutas)
├── templates/
│   └── index.html         # interfaz con polling a /estado cada 300 ms
├── tests/                 # pruebas automáticas (unittest)
├── benchmark.py           # experimentos: tabla de dificultad, efecto de hilos, ganadores
├── resultados/            # datos crudos de los experimentos (CSV)
├── docs/                  # capturas de pantalla
├── requirements.txt       # flask, cryptography
└── README.md
```

## Requisitos

- Python 3.10 o superior (probado con 3.13)
- `pip`

## Instalación

Desde la carpeta del proyecto, crea un entorno virtual e instala las dependencias.

**Windows (PowerShell)**

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Si PowerShell bloquea la activación, ejecuta una vez `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` y vuelve a intentar. En el símbolo del sistema (cmd) se activa con `.venv\Scripts\activate.bat`.

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Ejecución

```bash
python app.py
```

Abre <http://127.0.0.1:5000> en el navegador. El servidor se inicia con `app.run(debug=True, use_reloader=False)`: el recargador automático lanzaría un segundo proceso y duplicaría el estado global (cadena, nodos e hilos).

Para cambiar la dificultad sin tocar el código (por ejemplo, para pruebas rápidas):

```bash
# macOS / Linux
POW_DIFICULTAD=3 python app.py
```

```powershell
# Windows (PowerShell)
$env:POW_DIFICULTAD = "3"; python app.py
```

Por defecto `DIFICULTAD = 5` (≈ 1 048 576 intentos esperados por bloque, unos segundos).

## Cómo usarlo (guion de demostración)

1. **Nueva transacción:** elige remitente, destinatario y monto, y pulsa *Firmar transacción*. Alice, Beto y Carla empiezan con 100 monedas (asignación del bloque génesis).
2. **Minar:** pulsa *Minar*. Los cuatro carriles de la carrera se actualizan cada 300 ms con los intentos y el último hash que prueba cada nodo. Al terminar, la página se recarga y marca al ganador, su saldo (+50) y el nuevo bloque.
3. **Repite** al menos tres veces para tener varios bloques.
4. **Firma inválida:** marca *Alterar el monto después de firmar*, firma y pulsa *Minar*: la transacción se rechaza y no se lanza ningún hilo.
5. **Regla de saldo:** envía más monedas de las que tiene el remitente y se rechaza por saldo insuficiente.
6. **Alteración:** en la sección *Cadena de bloques* abre *Probar alteración*, elige un bloque y una forma de alterarlo (contenido, contenido y recálculo de hash, hash, firma, minero) y pulsa *Alterar bloque*. La cadena pasa a **Cadena inválida** y se listan los chequeos que fallaron. *Restaurar cadena* la deja como estaba.

## Rutas

| Ruta | Método | Qué hace |
|---|---|---|
| `/` | GET | Formulario, transacción pendiente, tabla de nodos y cadena con su validez |
| `/transaccion` | POST | Arma la transacción, la firma y la deja pendiente |
| `/minar` | POST | Verifica firma y reglas; crea un bloque por nodo y lanza un hilo por nodo |
| `/estado` | GET | JSON con `minando`, `ganador` y, por nodo, `intentos`, `ultimo`, `saldo` |
| `/alterar` | POST | (demostración) Altera un bloque ya minado sin recalcular |
| `/restaurar` | POST | (demostración) Deshace la alteración |

## Propósito: moneda con saldo

Transacción: `{proposito: "moneda", remitente: <llave pública>, contenido: {para, monto, secuencia}, hora}`.

Reglas validadas antes de minar y de nuevo al validar toda la cadena:

1. Firma Ed25519 válida del remitente.
2. `monto` entero positivo.
3. `saldo(remitente) ≥ monto`, con el saldo recalculado desde la cadena (asignación del génesis + recibido − enviado + recompensas).
4. `secuencia` = última secuencia del remitente + 1, lo que impide reenviar una transacción ya registrada.

## Pruebas

```bash
python -m unittest -v
```

Cubren: hash determinista, firma válida e inválida, partición de nonces, carrera sin bloques duplicados (200 rondas con 8 nodos y dificultad 1 para forzar colisiones), recompensa ligada al bloque, conservación de monedas, las cinco alteraciones de la cadena y las reglas de moneda.

## Benchmark de dificultad

```bash
python benchmark.py tabla                     # d = 3, 4, 5 → tabla del reporte
python benchmark.py hilos --d 4               # efecto del número de hilos
python benchmark.py ganadores --repeticiones 200
```

Resultados de referencia (servidor de 2 CPU lógicas, Python 3.13, ≈ 146 000 hash/s por hilo; 4 nodos):

| d | N | Intentos medios | Teórico 16^d | Tiempo medio (s) |
|---|---|---|---|---|
| 3 | 300 | 4 243 | 4 096 | 0.033 |
| 4 | 200 | 68 644 | 65 536 | 0.532 |
| 5 | 60 | 1 140 049 | 1 048 576 | 8.861 |

La razón de tiempos medios entre dificultades consecutivas fue 16.4 y 16.6 (teórico 16). Los datos completos están en `resultados/`.

Cada repetición mina un bloque distinto (varían la hora de la transacción y el `hash_anterior`); minar siempre el mismo bloque daría siempre el mismo nonce. Los datos crudos se guardan en `resultados/`. Los **tiempos dependen de tu equipo**; los **intentos** no, y deben acercarse a 16^d.

## Decisiones de diseño (resumen)

- **Dificultad en ceros hexadecimales:** `p = 16^(-d)` por intento, así que cada cero adicional multiplica por 16 el trabajo esperado.
- **Reparto `nonce_{i,k} = i + k·n`:** clases residuales módulo `n`, que son una partición exacta de los naturales.
- **`Event` + `Lock` con re-chequeo dentro de la sección crítica:** garantiza a lo sumo un bloque por ronda.
- **`minero` dentro del bloque:** copiar el bloque ganador para cobrar exige rehacer el trabajo.
- **Saldos derivados de la cadena** en lugar de un contador mutable, para que la recompensa quede ligada al bloque.
- **Orden de arranque aleatorio de los hilos:** con el GIL, el primer hilo iniciado corre ~5 ms antes de ceder; con orden fijo el nodo 0 ganaba ~40 % de las carreras cortas (χ² = 76.2) y con el sorteo la distribución es uniforme (χ² = 5.0, p = 0.17).
- **Génesis** exento de prueba de trabajo y de firma; reparte los saldos iniciales para que existan monedas que transferir.

## Limitaciones conocidas

- **GIL de CPython:** los hilos no aceleran el cálculo del hash; modelan la competencia y la sincronización, no el paralelismo real (se mide en `benchmark.py hilos`).
- Un solo proceso, sin red P2P, sin persistencia en disco ni resolución de bifurcaciones.
- Las llaves privadas viven en memoria y se regeneran en cada ejecución.
- `debug=True` es solo para desarrollo local; no exponer a internet.
