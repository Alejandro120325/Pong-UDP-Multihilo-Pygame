"""Pong UDP multihilo: servidor que controla fisica, puntos y partida.

Ejecucion: py servidor.py --bind 0.0.0.0 --puerto 5000
No necesita Pygame: solo utiliza bibliotecas de Python.
"""

import argparse
import json
import math
import socket
import threading
import time

ANCHO, ALTO = 1280, 720
PUERTO = 5000
RADIO = 20
RAQUETA_ANCHO, RAQUETA_ALTO = 20, 150
RAQUETA_X = {1: 40, 2: 1220}
VELOCIDAD_RAQUETA = 460
ACELERACION = 1.05              # +5 % por golpe de raqueta
VELOCIDAD_MAXIMA = 900
PUNTOS_PARA_GANAR = 7
DESCONEXION_SEGUNDOS = 8

# El estado de juego se comparte entre el hilo receptor y el principal.
candado = threading.Lock()
jugadores = {}  # (ip, puerto) -> informacion de cada jugador
raquetas = {1: 285.0, 2: 285.0}
puntos = {1: 0, 2: 0}
bola_x, bola_y = 640.0, 360.0
vx, vy = 350.0, 180.0
pausado = False
pausa_inicio = 0.0
preparacion_hasta = time.monotonic() + 2.0
ganador = None
dos_conectados_antes = False

# Eventos de rebote/punto para efectos visuales de ambos clientes.
evento_id = 0
evento_tipo = ""
evento_pos = [640, 360]


def enviar(sock, direccion, mensaje):
    try:
        sock.sendto(json.dumps(mensaje).encode("utf-8"), direccion)
    except OSError:
        pass


def registrar_evento(tipo, x, y):
    global evento_id, evento_tipo, evento_pos
    evento_id += 1
    evento_tipo = tipo
    evento_pos = [round(x), round(y)]


def reiniciar_bola(sentido=1):
    global bola_x, bola_y, vx, vy, preparacion_hasta
    bola_x, bola_y = ANCHO / 2, ALTO / 2
    vx, vy = 350.0 * sentido, 180.0
    preparacion_hasta = time.monotonic() + 1.5


def reiniciar_partida():
    global pausado, pausa_inicio, ganador
    puntos[1], puntos[2] = 0, 0
    raquetas[1], raquetas[2] = 285.0, 285.0
    ganador = None
    pausado = False
    pausa_inicio = 0.0
    reiniciar_bola()
    registrar_evento("reiniciar", 640, 360)


def recibir_mensajes(sock):
    """Hilo secundario: recibe solicitudes, teclas y comandos por UDP."""
    global pausado, pausa_inicio, preparacion_hasta

    while True:
        try:
            datos, direccion = sock.recvfrom(4096)
            mensaje = json.loads(datos.decode("utf-8"))
            if not isinstance(mensaje, dict):
                continue
        except socket.timeout:
            continue
        except (ValueError, UnicodeDecodeError):
            continue
        except OSError:
            break

        tipo = mensaje.get("tipo")
        respuesta = None
        with candado:
            jugador = jugadores.get(direccion)
            if tipo == "unirse":
                if jugador is None:
                    ocupados = {j["numero"] for j in jugadores.values()}
                    numero = next((n for n in (1, 2) if n not in ocupados), None)
                    if numero is None:
                        respuesta = {"tipo": "lleno"}
                    else:
                        jugador = {
                            "numero": numero, "arriba": False, "abajo": False,
                            "ultimo_paquete": time.monotonic(), "secuencia": -1,
                            "ultimo_comando": -1,
                        }
                        jugadores[direccion] = jugador
                        print(f"Jugador {numero} conectado: {direccion}", flush=True)
                if jugador is not None:
                    jugador["ultimo_paquete"] = time.monotonic()
                    respuesta = {"tipo": "bienvenido", "jugador": jugador["numero"]}

            elif tipo == "salir" and jugador is not None:
                print(f"Jugador {jugador['numero']} salio de la partida", flush=True)
                del jugadores[direccion]

            elif tipo == "controles" and jugador is not None:
                jugador["ultimo_paquete"] = time.monotonic()
                secuencia = mensaje.get("secuencia", -1)
                if type(secuencia) is not int or secuencia <= jugador["secuencia"]:
                    continue  # UDP puede duplicar o reordenar los paquetes.
                jugador["secuencia"] = secuencia
                jugador["arriba"] = mensaje.get("arriba") is True
                jugador["abajo"] = mensaje.get("abajo") is True

                comando = mensaje.get("comando")
                comando_id = mensaje.get("comando_id", -1)
                if (comando in ("p", "c", "r") and type(comando_id) is int
                        and comando_id > jugador["ultimo_comando"]):
                    jugador["ultimo_comando"] = comando_id
                    if comando == "p" and not pausado and ganador is None:
                        pausado = True
                        pausa_inicio = time.monotonic()
                        print(f"Jugador {jugador['numero']} PAUSO", flush=True)
                    elif comando == "c" and pausado:
                        pausado = False
                        preparacion_hasta += time.monotonic() - pausa_inicio
                        print(f"Jugador {jugador['numero']} CONTINUO", flush=True)
                    elif comando == "r":
                        reiniciar_partida()
                        print(f"Jugador {jugador['numero']} REINICIO", flush=True)

        if respuesta is not None:
            enviar(sock, direccion, respuesta)


def actualizar_juego(dt):
    """Hilo principal: mueve raquetas/bola y decide colisiones/puntos."""
    global bola_x, bola_y, vx, vy, ganador, dos_conectados_antes, preparacion_hasta
    ahora = time.monotonic()

    for direccion, jugador in list(jugadores.items()):
        if ahora - jugador["ultimo_paquete"] > DESCONEXION_SEGUNDOS:
            print(f"Jugador {jugador['numero']} desconectado", flush=True)
            del jugadores[direccion]

    ambos = len(jugadores) == 2
    if ambos and not dos_conectados_antes:
        preparacion_hasta = ahora + 1.5
        print("Dos jugadores listos. Comienza Pong.", flush=True)
    dos_conectados_antes = ambos

    if not ambos or pausado or ganador is not None:
        return

    # Movimiento autoritativo de cada raqueta.
    for jugador in jugadores.values():
        numero = jugador["numero"]
        movimiento = int(jugador["abajo"]) - int(jugador["arriba"])
        if ahora - jugador["ultimo_paquete"] > 0.35:
            movimiento = 0
        raquetas[numero] += movimiento * VELOCIDAD_RAQUETA * dt
        raquetas[numero] = max(0, min(ALTO - RAQUETA_ALTO, raquetas[numero]))

    if ahora < preparacion_hasta:
        return  # Breve cuenta regresiva antes del saque.

    bola_x += vx * dt
    bola_y += vy * dt

    # Rebotes techo/suelo.
    if bola_y - RADIO <= 0:
        bola_y = RADIO
        vy = abs(vy)
        registrar_evento("pared", bola_x, bola_y)
    elif bola_y + RADIO >= ALTO:
        bola_y = ALTO - RADIO
        vy = -abs(vy)
        registrar_evento("pared", bola_x, bola_y)

    # Rebote con raquetas: el punto de impacto cambia el angulo.
    for numero in (1, 2):
        x, y = RAQUETA_X[numero], raquetas[numero]
        cerca_y = bola_y + RADIO >= y and bola_y - RADIO <= y + RAQUETA_ALTO
        if numero == 1:
            cerca_x = bola_x - RADIO <= x + RAQUETA_ANCHO and bola_x + RADIO >= x
            acerca = vx < 0
        else:
            cerca_x = bola_x + RADIO >= x and bola_x - RADIO <= x + RAQUETA_ANCHO
            acerca = vx > 0

        if cerca_y and cerca_x and acerca:
            bola_x = x + RAQUETA_ANCHO + RADIO if numero == 1 else x - RADIO
            rapidez = min(math.hypot(vx, vy) * ACELERACION, VELOCIDAD_MAXIMA)
            impacto = (bola_y - (y + RAQUETA_ALTO / 2)) / (RAQUETA_ALTO / 2)
            impacto = max(-1.0, min(1.0, impacto))
            sentido = 1 if numero == 1 else -1
            dx, dy = sentido, impacto * 0.9
            norma = math.hypot(dx, dy)
            vx, vy = dx / norma * rapidez, dy / norma * rapidez
            registrar_evento("raqueta", bola_x, bola_y)
            print(f"Golpe jugador {numero}: {rapidez:.0f} px/s", flush=True)
            break

    # Si falla una raqueta, punto para el rival.
    anota = 2 if bola_x + RADIO < 0 else 1 if bola_x - RADIO > ANCHO else None
    if anota is not None:
        puntos[anota] += 1
        registrar_evento("punto", bola_x, bola_y)
        print(f"PUNTO J{anota}: {puntos[1]} - {puntos[2]}", flush=True)
        if puntos[anota] >= PUNTOS_PARA_GANAR:
            ganador = anota
            bola_x, bola_y = 640.0, 360.0
            print(f"¡GANADOR: JUGADOR {anota}! Presiona R para reiniciar", flush=True)
        else:
            reiniciar_bola(sentido=1 if anota == 2 else -1)


def obtener_estado(secuencia):
    return {
        "tipo": "estado", "secuencia_estado": secuencia,
        "bola": [round(bola_x, 2), round(bola_y, 2)],
        "raquetas": [round(raquetas[1], 2), round(raquetas[2], 2)],
        "puntos": [puntos[1], puntos[2]],
        "rapidez": round(math.hypot(vx, vy)),
        "pausado": pausado, "conectados": len(jugadores),
        "ganador": ganador,
        "espera": round(max(0, preparacion_hasta - (pausa_inicio if pausado else time.monotonic())), 2),
        "evento_id": evento_id, "evento_tipo": evento_tipo, "evento_pos": evento_pos,
        "meta": PUNTOS_PARA_GANAR,
    }


def main():
    parser = argparse.ArgumentParser(description="Servidor de Pong UDP multihilo")
    parser.add_argument("--bind", default="0.0.0.0", help="Interfaz de red del servidor")
    parser.add_argument("--puerto", type=int, default=PUERTO)
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.bind, args.puerto))
    sock.settimeout(0.25)
    threading.Thread(target=recibir_mensajes, args=(sock,), daemon=True).start()
    print(f"Servidor UDP escuchando en {args.bind}:{args.puerto}", flush=True)
    print("Esperando dos jugadores... Ctrl+C para detener", flush=True)

    anterior = time.perf_counter()
    ultimo_envio = 0.0
    secuencia_estado = 0
    try:
        while True:
            inicio = time.perf_counter()
            dt = min(inicio - anterior, 0.05)
            anterior = inicio
            with candado:
                actualizar_juego(dt)
                direcciones = list(jugadores)
                if inicio - ultimo_envio >= 1 / 30:
                    secuencia_estado += 1
                    datos = obtener_estado(secuencia_estado)
                else:
                    datos = None
            if datos is not None:
                for direccion in direcciones:
                    enviar(sock, direccion, datos)
                ultimo_envio = inicio
            time.sleep(max(0, 1 / 60 - (time.perf_counter() - inicio)))
    except KeyboardInterrupt:
        print("Servidor detenido")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
