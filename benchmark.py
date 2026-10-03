"""Experimentos de dificultad y concurrencia.

Uso (desde la raíz del proyecto):
    python benchmark.py tabla                       # d = 3, 4, 5  (tabla del reporte)
    python benchmark.py tabla --dificultades 3 4 --repeticiones 100 50
    python benchmark.py hilos --d 4 --repeticiones 40   # efecto del número de hilos (GIL)
    python benchmark.py ganadores --repeticiones 200    # uniformidad de los ganadores (chi-cuadrado)

Cada repetición mina un bloque DISTINTO (cambia la hora de la transacción y el hash_anterior).
Si se minara siempre el mismo bloque, el hash sería idéntico y el nonce hallado también:
varianza cero y un resultado engañoso.
"""
import argparse
import csv
import math
import os
import platform
import statistics as st
import sys
import time

from blockchain import Simulador, cumple_dificultad, hash_bloque

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resultados")


def tasa_un_hilo(n=150_000):
    """Hashes por segundo de un solo hilo (hash_bloque sobre un bloque típico)."""
    from blockchain import Billetera, crear_transaccion
    a, b = Billetera("a"), Billetera("b")
    tx = crear_transaccion(a.pub, b.pub, 1, 1)
    blq = {"numero": 1, "nonce": 0, "transaccion": tx, "firma": a.firmar(tx),
           "hash_anterior": "0" * 64, "minero": a.pub}
    t0 = time.perf_counter()
    for i in range(n):
        blq["nonce"] = i
        hash_bloque(blq)
    return n / (time.perf_counter() - t0)


def carreras(d, reps, n_nodos=4):
    """Lista de (duracion_s, intentos_totales, ganador) de `reps` carreras reales con hilos."""
    sim = Simulador(dificultad=d, n_nodos=n_nodos, saldo_inicial=10 ** 9)
    filas = []
    for _ in range(reps):
        sim.crear_transaccion("Alice", "Beto", 1)
        est = sim.minar_y_esperar()
        filas.append((est["duracion"], est["intentos_total"], est["ganador"]))
    assert sim.cadena.es_valida()
    return filas


def cabecera():
    print(f"# Python {platform.python_version()} · {platform.system()} {platform.machine()} · "
          f"{os.cpu_count()} CPU lógicas · {time.strftime('%Y-%m-%d %H:%M')}")


def guardar_csv(nombre, encabezado, filas):
    os.makedirs(DIR, exist_ok=True)
    ruta = os.path.join(DIR, nombre)
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(encabezado)
        w.writerows(filas)
    print(f"(datos crudos en {os.path.relpath(ruta)})")


def cmd_tabla(a):
    cabecera()
    H1 = tasa_un_hilo()
    print(f"Tasa de un solo hilo: H1 = {H1:,.0f} hash/s\n")
    reps = a.repeticiones + [a.repeticiones[-1]] * (len(a.dificultades) - len(a.repeticiones))
    crudo = []
    print("| d | N | Intentos medios (4 nodos) | Teórico 16^d | Media/Teórico | Desv. est. | Mediana | Tiempo medio (s) | IC 95 % tiempo (s) | Tiempo teórico 16^d/H (s) |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for d, r in zip(a.dificultades, reps):
        filas = carreras(d, r)
        t = [f[0] for f in filas]
        k = [f[1] for f in filas]
        for i, f in enumerate(filas):
            crudo.append((d, i + 1, f[0], f[1], f[2]))
        mk, mt = st.mean(k), st.mean(t)
        sk = st.stdev(k) if r > 1 else float("nan")
        ic = 1.96 * st.stdev(t) / math.sqrt(r) if r > 1 else float("nan")
        H = sum(k) / sum(t)                          # tasa total efectiva con los hilos
        print(f"| {d} | {r} | {mk:,.0f} | {16 ** d:,} | {mk / 16 ** d:.2f} | {sk:,.0f} | {st.median(k):,.0f} "
              f"| {mt:.3f} | ±{ic:.3f} | {16 ** d / H:.3f} |", flush=True)
    guardar_csv("benchmark_dificultad.csv", ["d", "repeticion", "duracion_s", "intentos_totales", "ganador"], crudo)
    ds = a.dificultades
    medias = {d: st.mean([c[2] for c in crudo if c[0] == d]) for d in ds}
    for d1, d2 in zip(ds, ds[1:]):
        print(f"Razón de tiempos medios d={d2} / d={d1}: {medias[d2] / medias[d1]:.1f} (teórico {16 ** (d2 - d1)})")


def cmd_hilos(a):
    cabecera()
    H1 = tasa_un_hilo()
    print(f"Tasa de un solo hilo: H1 = {H1:,.0f} hash/s (d = {a.d}, {a.repeticiones} carreras por fila)\n")
    print("| Nodos (hilos) | Intentos medios | Tiempo medio (s) | Hash/s totales | Aceleración vs 1 nodo |")
    print("|---|---|---|---|---|")
    base = None
    crudo = []
    for n in a.nodos:
        filas = carreras(a.d, a.repeticiones, n_nodos=n)
        t = sum(f[0] for f in filas)
        k = sum(f[1] for f in filas)
        tasa = k / t
        base = tasa if base is None else base
        crudo.append((n, st.mean([f[0] for f in filas]), k / len(filas), tasa))
        print(f"| {n} | {k / len(filas):,.0f} | {t / len(filas):.3f} | {tasa:,.0f} | {tasa / base:.2f}x |", flush=True)
    guardar_csv("benchmark_hilos.csv", ["nodos", "tiempo_medio_s", "intentos_medios", "hash_por_s"], crudo)


def sf_chi2_3gl(x):
    """P(X > x) para chi-cuadrado con 3 grados de libertad (forma cerrada)."""
    return math.erfc(math.sqrt(x / 2)) + math.sqrt(2 * x / math.pi) * math.exp(-x / 2)


def cmd_ganadores(a):
    cabecera()
    filas = carreras(a.d, a.repeticiones, n_nodos=4)
    cuentas = {f"Nodo {i}": 0 for i in range(4)}
    for f in filas:
        cuentas[f[2]] += 1
    esperado = a.repeticiones / 4
    chi2 = sum((c - esperado) ** 2 / esperado for c in cuentas.values())
    print(f"d = {a.d}, {a.repeticiones} carreras, esperado por nodo = {esperado:.1f}")
    for n, c in cuentas.items():
        print(f"  {n}: {c} victorias")
    print(f"chi^2 = {chi2:.2f} (3 g.l.; crítico al 5 % = 7.81); p = {sf_chi2_3gl(chi2):.3f}")
    print("No se rechaza la uniformidad." if chi2 < 7.81 else "Se rechaza la uniformidad al 5 %.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tabla")
    t.add_argument("--dificultades", type=int, nargs="+", default=[3, 4, 5])
    t.add_argument("--repeticiones", type=int, nargs="+", default=[100, 60, 30])
    t.set_defaults(f=cmd_tabla)
    h = sub.add_parser("hilos")
    h.add_argument("--d", type=int, default=4)
    h.add_argument("--nodos", type=int, nargs="+", default=[1, 2, 4, 8])
    h.add_argument("--repeticiones", type=int, default=40)
    h.set_defaults(f=cmd_hilos)
    g = sub.add_parser("ganadores")
    g.add_argument("--d", type=int, default=3)
    g.add_argument("--repeticiones", type=int, default=200)
    g.set_defaults(f=cmd_ganadores)
    args = p.parse_args()
    args.f(args)
