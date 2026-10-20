"""Pong Neon: cliente grafico de Pong UDP con sockets y un hilo receptor.

Jugador 1: W/S | Jugador 2: flechas | P: pausa | C: sigue | R: reinicia
Ejemplo: py cliente.py --host 172.17.44.40 --puerto 5000
Requiere: pip install pygame-ce
"""

import argparse
from collections import deque
import json
import random
import socket
import threading
import time

import pygame

ANCHO, ALTO, PUERTO = 1280, 720, 5000
ALTO_BARRA = 64  # Reservamos este espacio para controles e informacion.
ALTO_CANCHA_VISIBLE = ALTO - ALTO_BARRA
RADIO = 20
FONDO = (9, 17, 31)
BLANCO = (238, 247, 255)
GRIS = (155, 174, 199)
TURQUESA = (61, 224, 202)    # Jugador 1
CORAL = (255, 127, 147)      # Jugador 2


def texto(pantalla, fuente, mensaje, color, centro):
    imagen = fuente.render(str(mensaje), True, color)
    pantalla.blit(imagen, imagen.get_rect(center=centro))


def fondo_precalculado():
    """Degradado creado una vez para no hacerlo en cada fotograma."""
    imagen = pygame.Surface((ANCHO, ALTO))
    arriba = (8, 17, 32)
    abajo = (17, 34, 56)
    for y in range(ALTO):
        t = y / ALTO
        color = tuple(round(a + (b - a) * t) for a, b in zip(arriba, abajo))
        pygame.draw.line(imagen, color, (0, y), (ANCHO, y))
    return imagen


def dibujar_raqueta(pantalla, x, y, color):
    rectangulo = pygame.Rect(x, round(y), 20, 150)
    # Un brillo ligero y la raqueta principal redondeada.
    brillo = pygame.Surface((52, 176), pygame.SRCALPHA)
    pygame.draw.rect(brillo, (*color, 37), (9, 5, 34, 166), border_radius=16)
    pantalla.blit(brillo, (x - 16, round(y) - 13))
    pygame.draw.rect(pantalla, color, rectangulo, border_radius=9)
    pygame.draw.rect(pantalla, BLANCO, (x + 5, round(y) + 10, 3, 130), border_radius=2)


def dibujar_bola(pantalla, x, y, historial):
    # Estela corta que muestra la direccion de la pelota.
    for indice, (px, py) in enumerate(historial):
        radio = 5 + indice * 2
        mezcla = indice / max(1, len(historial))
        color = (round(40 + 120 * mezcla), round(110 + 120 * mezcla), 235)
        pygame.draw.circle(pantalla, color, (round(px), round(py)), radio)
    halo = pygame.Surface((110, 110), pygame.SRCALPHA)
    pygame.draw.circle(halo, (93, 193, 255, 28), (55, 55), 47)
    pygame.draw.circle(halo, (122, 220, 255, 47), (55, 55), 33)
    pantalla.blit(halo, (round(x) - 55, round(y) - 55))
    pygame.draw.circle(pantalla, BLANCO, (round(x), round(y)), RADIO)
    pygame.draw.circle(pantalla, (198, 232, 255), (round(x) - 5, round(y) - 6), 5)


def dibujar_cuadro(pantalla, fuente_grande, fuente_pequena, titulo, subtitulo, color):
    """Cuadro central para espera, pausa, resultado o desconexion."""
    sombra = pygame.Surface((630, 174), pygame.SRCALPHA)
    pygame.draw.rect(sombra, (4, 12, 27, 228), (0, 0, 630, 174), border_radius=20)
    pygame.draw.rect(sombra, (*color, 120), (0, 0, 630, 174), width=2, border_radius=20)
    pantalla.blit(sombra, (325, 273))
    texto(pantalla, fuente_grande, titulo, color, (640, 344))
    texto(pantalla, fuente_pequena, subtitulo, BLANCO, (640, 400))


def main():
    parser = argparse.ArgumentParser(description="Pong Neon - cliente UDP")
    parser.add_argument("--host", default="127.0.0.1", help="IP de la laptop servidora")
    parser.add_argument("--puerto", type=int, default=PUERTO)
    args = parser.parse_args()
    servidor = (socket.gethostbyname(args.host), args.puerto)

    pygame.init()
    pantalla = pygame.display.set_mode((ANCHO, ALTO))
    pygame.display.set_caption("PONG NEON | UDP MULTIHILO")
    reloj = pygame.time.Clock()
    fondo = fondo_precalculado()
    # La cancha mantiene las coordenadas originales del servidor (1280 x 720).
    # Se dibuja aparte y luego se ajusta a la zona superior de la ventana.
    cancha = pygame.Surface((ANCHO, ALTO)).convert()
    cancha_ajustada = pygame.Surface((ANCHO, ALTO_CANCHA_VISIBLE)).convert()
    grande = pygame.font.Font(None, 90)
    mediana = pygame.font.Font(None, 43)
    normal = pygame.font.Font(None, 29)
    pequena = pygame.font.Font(None, 23)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.25)
    candado = threading.Lock()
    compartido = {
        "jugador": None, "estado": None, "ultimo_estado": time.monotonic(),
        "ultima_secuencia": -1, "lleno": False,
    }
    activo = threading.Event()
    activo.set()

    def enviar(mensaje):
        try:
            sock.sendto(json.dumps(mensaje).encode("utf-8"), servidor)
        except OSError:
            pass

    def recibir():
        """Hilo secundario: escucha UDP mientras Pygame dibuja sin bloquearse."""
        while activo.is_set():
            try:
                datos, direccion = sock.recvfrom(4096)
                if direccion != servidor:
                    continue
                mensaje = json.loads(datos.decode("utf-8"))
                if not isinstance(mensaje, dict):
                    continue
            except socket.timeout:
                continue
            except (ValueError, UnicodeDecodeError):
                continue
            except OSError:
                break

            with candado:
                tipo = mensaje.get("tipo")
                if tipo == "bienvenido":
                    compartido["jugador"] = mensaje.get("jugador")
                    compartido["lleno"] = False
                    compartido["ultimo_estado"] = time.monotonic()
                    # En una reconexion, los numeros de secuencia pueden empezar de cero.
                    compartido["ultima_secuencia"] = -1
                    print(f"Conectado: JUGADOR {compartido['jugador']}", flush=True)
                elif tipo == "estado":
                    secuencia = mensaje.get("secuencia_estado", -1)
                    if type(secuencia) is not int or secuencia <= compartido["ultima_secuencia"]:
                        continue
                    compartido["ultima_secuencia"] = secuencia
                    compartido["estado"] = mensaje
                    compartido["ultimo_estado"] = time.monotonic()
                elif tipo == "lleno":
                    compartido["lleno"] = True

    threading.Thread(target=recibir, daemon=True).start()

    historial = deque(maxlen=6)
    particulas = []  # Solo un efecto visual: no modifica la fisica del servidor.
    ultimo_evento = -1
    ultima_secuencia_dibujada = -1
    siguiente_union = 0.0
    siguiente_envio = 0.0
    secuencia = 0
    comando_id = 0
    comando_pendiente = None
    comando_hasta = 0.0
    jugando = True

    try:
        while jugando:
            ahora = time.monotonic()
            for evento in pygame.event.get():
                if evento.type == pygame.QUIT:
                    jugando = False
                elif evento.type == pygame.KEYDOWN:
                    if evento.key == pygame.K_ESCAPE:
                        jugando = False
                    elif evento.key in (pygame.K_p, pygame.K_c, pygame.K_r):
                        comando_pendiente = {
                            pygame.K_p: "p", pygame.K_c: "c", pygame.K_r: "r",
                        }[evento.key]
                        comando_id += 1
                        comando_hasta = ahora + 0.7  # Repetir orden ante perdidas UDP.

            with candado:
                if compartido["jugador"] is not None and ahora - compartido["ultimo_estado"] > 3:
                    compartido["jugador"] = None
                    compartido["estado"] = None
                    compartido["ultima_secuencia"] = -1
                    historial.clear()
                numero = compartido["jugador"]
                estado = compartido["estado"]
                lleno = compartido["lleno"]

            if numero is None:
                if ahora >= siguiente_union:
                    enviar({"tipo": "unirse"})
                    siguiente_union = ahora + 1
            elif ahora >= siguiente_envio:
                teclas = pygame.key.get_pressed()
                if numero == 1:
                    subir, bajar = bool(teclas[pygame.K_w]), bool(teclas[pygame.K_s])
                else:
                    subir, bajar = bool(teclas[pygame.K_UP]), bool(teclas[pygame.K_DOWN])
                secuencia += 1
                paquete = {
                    "tipo": "controles", "secuencia": secuencia,
                    "arriba": subir, "abajo": bajar,
                }
                if comando_pendiente and ahora < comando_hasta:
                    paquete["comando"] = comando_pendiente
                    paquete["comando_id"] = comando_id
                enviar(paquete)
                siguiente_envio = ahora + 1 / 30

            # Interfaz: igual para ambos jugadores, sincronizada por el servidor.
            cancha.blit(fondo, (0, 0))
            for y in range(143, ALTO - 45, 29):
                pygame.draw.rect(cancha, (54, 76, 102), (638, y, 4, 14), border_radius=2)
            pygame.draw.circle(cancha, (39, 64, 88), (640, 365), 99, 2)
            pygame.draw.line(cancha, (36, 60, 82), (0, 2), (ANCHO, 2), 3)
            pygame.draw.line(cancha, (36, 60, 82), (0, ALTO - 2), (ANCHO, ALTO - 2), 3)
            texto(cancha, normal, "PONG  /  UDP MULTIHILO", BLANCO, (640, 37))
            texto(cancha, pequena, "PRIMERO EN LLEGAR A 7", GRIS, (640, 102))
            texto(cancha, normal, "JUGADOR 1", TURQUESA, (320, 36))
            texto(cancha, normal, "JUGADOR 2", CORAL, (960, 36))

            # Marcador.
            puntajes = estado.get("puntos", [0, 0]) if estado else [0, 0]
            texto(cancha, grande, puntajes[0], BLANCO, (320, 105))
            texto(cancha, grande, puntajes[1], BLANCO, (960, 105))
            pygame.draw.line(cancha, (31, 49, 70), (100, 151), (1180, 151), 1)

            if estado is not None:
                pelota_x, pelota_y = estado["bola"]
                y1, y2 = estado["raquetas"]
                dibujar_raqueta(cancha, 40, y1, TURQUESA)
                dibujar_raqueta(cancha, 1220, y2, CORAL)
                sec_estado = estado.get("secuencia_estado", -1)
                if sec_estado != ultima_secuencia_dibujada:
                    if historial and abs(pelota_x - historial[-1][0]) > 130:
                        historial.clear()  # No dibujar una estela larga tras un punto.
                    historial.append((pelota_x, pelota_y))
                    ultima_secuencia_dibujada = sec_estado
                dibujar_bola(cancha, pelota_x, pelota_y, list(historial)[:-1])

                # Destellos breves cuando la bola toca algo.
                id_evento = estado.get("evento_id", 0)
                if id_evento != ultimo_evento:
                    ultimo_evento = id_evento
                    tipo = estado.get("evento_tipo")
                    if tipo in ("raqueta", "pared"):
                        px, py = estado.get("evento_pos", [pelota_x, pelota_y])
                        for _ in range(10):
                            particulas.append([
                                float(px), float(py), random.uniform(-150, 150),
                                random.uniform(-150, 150), ahora + 0.3,
                            ])
                    elif tipo in ("reiniciar", "punto"):
                        historial.clear()

            # Efectos de particulas. El servidor NO depende de ellos.
            vigentes = []
            for particula in particulas:
                x, y, dx, dy, fin = particula
                if ahora >= fin:
                    continue
                x += dx / 60
                y += dy / 60
                radio_particula = max(1, round((fin - ahora) * 12))
                pygame.draw.circle(cancha, (113, 227, 255), (round(x), round(y)), radio_particula)
                vigentes.append([x, y, dx, dy, fin])
            particulas = vigentes

            # Avisos de inicio, pausa, desconexion y victoria.
            if numero is None:
                titulo = "SERVIDOR LLENO" if lleno else "CONECTANDO..."
                detalle = "Intenta de nuevo cuando se libere un lugar" if lleno else f"Buscando servidor {args.host}:{args.puerto}"
                dibujar_cuadro(cancha, mediana, normal, titulo, detalle, GRIS)
            elif estado is None or estado.get("conectados", 0) < 2:
                dibujar_cuadro(cancha, mediana, normal, "ESPERANDO RIVAL", "Abre el segundo cliente para jugar", TURQUESA)
            elif estado.get("ganador"):
                ganador = estado["ganador"]
                color = TURQUESA if ganador == 1 else CORAL
                dibujar_cuadro(cancha, mediana, normal, f"¡GANA JUGADOR {ganador}!", "Presiona R para una revancha", color)
            elif estado.get("pausado"):
                dibujar_cuadro(cancha, mediana, normal, "PARTIDA EN PAUSA", "Presiona C para continuar", BLANCO)
            elif estado.get("espera", 0) > 0:
                segundos = max(1, int(estado["espera"] + 0.99))
                dibujar_cuadro(cancha, mediana, normal, "¡PREPARADOS!", f"Saque en {segundos}...", TURQUESA)

            # Pintar la cancha completa (simulada por el servidor en 1280x720),
            # ajustada al espacio superior. La bola y las raquetas quedan enteras
            # dentro de ese espacio, sin desaparecer detras del panel inferior.
            pygame.transform.smoothscale(
                cancha, (ANCHO, ALTO_CANCHA_VISIBLE), cancha_ajustada
            )
            pantalla.blit(cancha_ajustada, (0, 0))

            # Banda inferior independiente, dibujada por DEBAJO de la cancha.
            pygame.draw.rect(
                pantalla, (15, 29, 48),
                (0, ALTO_CANCHA_VISIBLE, ANCHO, ALTO_BARRA)
            )
            pygame.draw.line(
                pantalla, (41, 65, 85),
                (0, ALTO_CANCHA_VISIBLE), (ANCHO, ALTO_CANCHA_VISIBLE), 2
            )
            texto(pantalla, pequena,
                  "J1  W / S        J2  FLECHAS        P  PAUSA        C  CONTINUAR        R  REINICIAR        ESC  SALIR",
                  BLANCO, (640, 676))
            info_velocidad = f"PELOTA  {estado['rapidez']} px/s" if estado else "PELOTA  --"
            texto(pantalla, pequena, info_velocidad, GRIS, (640, 701))
            if numero is not None:
                tu_color = TURQUESA if numero == 1 else CORAL
                texto(pantalla, pequena, f"ERES JUGADOR {numero}", tu_color, (123, 701))

            pygame.display.flip()
            reloj.tick(60)
    finally:
        enviar({"tipo": "salir"})
        activo.clear()
        sock.close()
        pygame.quit()


if __name__ == "__main__":
    main()
