"""Cliente Pygame de Pong Neón: POO, UDP y un hilo de recepción.

Ejemplo en Ubuntu: python3 cliente.py --host 172.17.44.106 --puerto 5000
Ejemplo en Windows: py cliente.py --host 127.0.0.1 --puerto 5000
Requisito: pip install pygame-ce
"""

import argparse
from collections import deque
import json
import random
import socket
import threading
import time

import pygame


class ConfiguracionVisual:
    """Constantes visuales agrupadas en un objeto de configuración."""

    def __init__(self):
        self.ancho = 1280
        self.alto = 720
        self.barra = 64
        self.cancha_visible = self.alto - self.barra
        self.radio = 20
        self.fondo = (9, 17, 31)
        self.blanco = (238, 247, 255)
        self.gris = (155, 174, 199)
        self.turquesa = (61, 224, 202)
        self.coral = (255, 127, 147)


class ConexionUDP:
    """Comunicación del cliente; recibe mensajes en un hilo independiente."""

    def __init__(self, host: str, puerto: int):
        self.direccion_servidor = (socket.gethostbyname(host), puerto)
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.settimeout(0.25)
        self._candado = threading.Lock()
        self._activo = threading.Event()
        self._activo.set()
        self._jugador: int | None = None
        self._estado: dict | None = None
        self._ultimo_estado = time.monotonic()
        self._ultima_secuencia = -1
        self._lleno = False
        self._hilo = threading.Thread(target=self._recibir, name="ClienteReceptorUDP", daemon=True)
        self._hilo.start()

    def enviar(self, mensaje: dict) -> None:
        try:
            self._socket.sendto(json.dumps(mensaje).encode("utf-8"), self.direccion_servidor)
        except OSError:
            pass

    def _recibir(self) -> None:
        while self._activo.is_set():
            try:
                datos, direccion = self._socket.recvfrom(4096)
                if direccion != self.direccion_servidor:
                    continue
                mensaje = json.loads(datos.decode("utf-8"))
                if not isinstance(mensaje, dict):
                    continue
            except (socket.timeout, ValueError, UnicodeDecodeError):
                continue
            except OSError:
                break
            with self._candado:
                tipo = mensaje.get("tipo")
                if tipo == "bienvenido":
                    numero = mensaje.get("jugador")
                    if numero not in (1, 2):
                        continue
                    # El servidor puede responder a varios 'unirse' al conectarse.
                    if self._jugador != numero:
                        print(f"Conectado: JUGADOR {numero}", flush=True)
                    self._jugador = numero
                    self._lleno = False
                    self._ultimo_estado = time.monotonic()
                    self._ultima_secuencia = -1
                elif tipo == "estado":
                    secuencia = mensaje.get("secuencia_estado", -1)
                    if type(secuencia) is not int or secuencia <= self._ultima_secuencia:
                        continue
                    self._ultima_secuencia = secuencia
                    self._estado = mensaje
                    self._ultimo_estado = time.monotonic()
                elif tipo == "lleno":
                    self._lleno = True

    def obtener_datos(self) -> tuple[int | None, dict | None, bool]:
        """Acceso seguro al estado compartido por los dos hilos."""
        with self._candado:
            if self._jugador is not None and time.monotonic() - self._ultimo_estado > 3:
                self._jugador = None
                self._estado = None
                self._ultima_secuencia = -1
            return self._jugador, self._estado, self._lleno

    def cerrar(self) -> None:
        self.enviar({"tipo": "salir"})
        self._activo.clear()
        self._socket.close()
        self._hilo.join(timeout=0.5)


class ControladorEntrada:
    """Responsabilidad exclusiva: traducir teclado a comandos del juego."""

    def __init__(self):
        self._comando_id = 0
        self._comando_pendiente: str | None = None
        self._repetir_hasta = 0.0

    def procesar_evento(self, evento: pygame.event.Event, ahora: float) -> bool:
        """Devuelve False cuando la ventana debe cerrarse."""
        if evento.type == pygame.QUIT:
            return False
        if evento.type == pygame.KEYDOWN:
            if evento.key == pygame.K_ESCAPE:
                return False
            comandos = {pygame.K_p: "p", pygame.K_c: "c", pygame.K_r: "r"}
            if evento.key in comandos:
                self._comando_pendiente = comandos[evento.key]
                self._comando_id += 1
                self._repetir_hasta = ahora + 0.7
        return True

    def crear_paquete(self, numero: int, secuencia: int, ahora: float) -> dict:
        keys = pygame.key.get_pressed()
        if numero == 1:
            arriba, abajo = bool(keys[pygame.K_w]), bool(keys[pygame.K_s])
        else:
            arriba, abajo = bool(keys[pygame.K_UP]), bool(keys[pygame.K_DOWN])
        paquete = {
            "tipo": "controles", "secuencia": secuencia,
            "arriba": arriba, "abajo": abajo,
        }
        if self._comando_pendiente and ahora < self._repetir_hasta:
            paquete["comando"] = self._comando_pendiente
            paquete["comando_id"] = self._comando_id
        return paquete


class EfectosVisuales:
    """Mantiene partículas y estela sin alterar la lógica de físicas."""

    def __init__(self):
        self.historial = deque(maxlen=6)
        self.particulas: list[list[float]] = []
        self.ultimo_evento = -1
        self.ultima_secuencia_dibujada = -1

    def limpiar(self) -> None:
        self.historial.clear()
        self.particulas.clear()
        self.ultima_secuencia_dibujada = -1

    def sincronizar(self, estado: dict | None, ahora: float) -> None:
        if not estado:
            return
        pelota_x, pelota_y = estado["bola"]
        secuencia = estado.get("secuencia_estado", -1)
        if secuencia != self.ultima_secuencia_dibujada:
            if self.historial and abs(pelota_x - self.historial[-1][0]) > 130:
                self.historial.clear()
            self.historial.append((pelota_x, pelota_y))
            self.ultima_secuencia_dibujada = secuencia
        evento_id = estado.get("evento_id", 0)
        if evento_id != self.ultimo_evento:
            self.ultimo_evento = evento_id
            tipo = estado.get("evento_tipo")
            if tipo in ("raqueta", "pared"):
                px, py = estado.get("evento_pos", [pelota_x, pelota_y])
                for _ in range(10):
                    self.particulas.append([
                        float(px), float(py), random.uniform(-150, 150),
                        random.uniform(-150, 150), ahora + 0.3,
                    ])
            elif tipo in ("punto", "reiniciar"):
                self.historial.clear()

    def dibujar_particulas(self, cancha: pygame.Surface, ahora: float, dt: float) -> None:
        vigentes = []
        for x, y, dx, dy, fin in self.particulas:
            if ahora >= fin:
                continue
            x += dx * dt
            y += dy * dt
            radio = max(1, round((fin - ahora) * 12))
            pygame.draw.circle(cancha, (113, 227, 255), (round(x), round(y)), radio)
            vigentes.append([x, y, dx, dy, fin])
        self.particulas = vigentes


class VistaPongNeon:
    """Vista: dibuja Pygame. No conoce sockets ni modifica puntuaciones."""

    def __init__(self, config: ConfiguracionVisual):
        self.config = config
        pygame.init()
        self.pantalla = pygame.display.set_mode((config.ancho, config.alto))
        pygame.display.set_caption("PONG NEON | UDP MULTIHILO | POO & SOLID")
        self.reloj = pygame.time.Clock()
        self._fondo = self._crear_fondo()
        self._cancha = pygame.Surface((config.ancho, config.alto)).convert()
        self._cancha_ajustada = pygame.Surface((config.ancho, config.cancha_visible)).convert()
        self._grande = pygame.font.Font(None, 90)
        self._mediana = pygame.font.Font(None, 43)
        self._normal = pygame.font.Font(None, 29)
        self._pequena = pygame.font.Font(None, 23)

    def _crear_fondo(self) -> pygame.Surface:
        c = self.config
        imagen = pygame.Surface((c.ancho, c.alto))
        arriba, abajo = (8, 17, 32), (17, 34, 56)
        for y in range(c.alto):
            t = y / c.alto
            color = tuple(round(a + (b - a) * t) for a, b in zip(arriba, abajo))
            pygame.draw.line(imagen, color, (0, y), (c.ancho, y))
        return imagen

    def _texto(self, fuente: pygame.font.Font, mensaje, color, centro, superficie=None) -> None:
        superficie = superficie if superficie is not None else self._cancha
        imagen = fuente.render(str(mensaje), True, color)
        superficie.blit(imagen, imagen.get_rect(center=centro))

    def _raqueta(self, x: int, y: float, color) -> None:
        rectangulo = pygame.Rect(x, round(y), 20, 150)
        brillo = pygame.Surface((52, 176), pygame.SRCALPHA)
        pygame.draw.rect(brillo, (*color, 37), (9, 5, 34, 166), border_radius=16)
        self._cancha.blit(brillo, (x - 16, round(y) - 13))
        pygame.draw.rect(self._cancha, color, rectangulo, border_radius=9)
        pygame.draw.rect(self._cancha, self.config.blanco,
                         (x + 5, round(y) + 10, 3, 130), border_radius=2)

    def _bola(self, x: float, y: float, historial) -> None:
        for indice, (px, py) in enumerate(historial):
            radio = 5 + indice * 2
            mezcla = indice / max(1, len(historial))
            color = (round(40 + 120 * mezcla), round(110 + 120 * mezcla), 235)
            pygame.draw.circle(self._cancha, color, (round(px), round(py)), radio)
        halo = pygame.Surface((110, 110), pygame.SRCALPHA)
        pygame.draw.circle(halo, (93, 193, 255, 28), (55, 55), 47)
        pygame.draw.circle(halo, (122, 220, 255, 47), (55, 55), 33)
        self._cancha.blit(halo, (round(x) - 55, round(y) - 55))
        pygame.draw.circle(self._cancha, self.config.blanco, (round(x), round(y)), self.config.radio)
        pygame.draw.circle(self._cancha, (198, 232, 255), (round(x) - 5, round(y) - 6), 5)

    def _aviso(self, titulo: str, subtitulo: str, color) -> None:
        sombra = pygame.Surface((630, 174), pygame.SRCALPHA)
        pygame.draw.rect(sombra, (4, 12, 27, 228), (0, 0, 630, 174), border_radius=20)
        pygame.draw.rect(sombra, (*color, 120), (0, 0, 630, 174), width=2, border_radius=20)
        self._cancha.blit(sombra, (325, 273))
        self._texto(self._mediana, titulo, color, (640, 344))
        self._texto(self._normal, subtitulo, self.config.blanco, (640, 400))

    def dibujar(self, jugador: int | None, estado: dict | None,
               lleno: bool, host: str, puerto: int,
               efectos: EfectosVisuales, ahora: float, dt: float) -> None:
        c = self.config
        self._cancha.blit(self._fondo, (0, 0))
        for y in range(143, c.alto - 45, 29):
            pygame.draw.rect(self._cancha, (54, 76, 102), (638, y, 4, 14), border_radius=2)
        pygame.draw.circle(self._cancha, (39, 64, 88), (640, 365), 99, 2)
        pygame.draw.line(self._cancha, (36, 60, 82), (0, 2), (c.ancho, 2), 3)
        pygame.draw.line(self._cancha, (36, 60, 82), (0, c.alto - 2), (c.ancho, c.alto - 2), 3)
        self._texto(self._normal, "PONG  /  UDP MULTIHILO", c.blanco, (640, 37))
        meta = estado.get("meta", 7) if estado else 7
        self._texto(self._pequena, f"PRIMERO EN LLEGAR A {meta}", c.gris, (640, 102))
        self._texto(self._normal, "JUGADOR 1", c.turquesa, (320, 36))
        self._texto(self._normal, "JUGADOR 2", c.coral, (960, 36))
        puntos = estado.get("puntos", [0, 0]) if estado else [0, 0]
        self._texto(self._grande, puntos[0], c.blanco, (320, 105))
        self._texto(self._grande, puntos[1], c.blanco, (960, 105))
        pygame.draw.line(self._cancha, (31, 49, 70), (100, 151), (1180, 151), 1)

        efectos.sincronizar(estado, ahora)
        if estado is not None:
            x, y = estado["bola"]
            y1, y2 = estado["raquetas"]
            self._raqueta(40, y1, c.turquesa)
            self._raqueta(1220, y2, c.coral)
            self._bola(x, y, list(efectos.historial)[:-1])
        efectos.dibujar_particulas(self._cancha, ahora, dt)

        if jugador is None:
            titulo = "SERVIDOR LLENO" if lleno else "CONECTANDO..."
            detalle = "Intenta cuando se libere un lugar" if lleno else f"Buscando servidor {host}:{puerto}"
            self._aviso(titulo, detalle, c.gris)
        elif estado is None or estado.get("conectados", 0) < 2:
            self._aviso("ESPERANDO RIVAL", "Abre el segundo cliente para jugar", c.turquesa)
        elif estado.get("ganador"):
            ganador = estado["ganador"]
            color = c.turquesa if ganador == 1 else c.coral
            self._aviso(f"¡GANA JUGADOR {ganador}!", "Presiona R para una revancha", color)
        elif estado.get("pausado"):
            self._aviso("PARTIDA EN PAUSA", "Presiona C para continuar", c.blanco)
        elif estado.get("espera", 0) > 0:
            segundos = max(1, int(estado["espera"] + 0.99))
            self._aviso("¡PREPARADOS!", f"Saque en {segundos}...", c.turquesa)

        # El servidor conserva coordenadas 1280x720. Reducimos únicamente la
        # visualización del campo: la barra de controles jamás oculta la pelota.
        pygame.transform.smoothscale(self._cancha,
                                     (c.ancho, c.cancha_visible), self._cancha_ajustada)
        self.pantalla.blit(self._cancha_ajustada, (0, 0))
        pygame.draw.rect(self.pantalla, (15, 29, 48),
                         (0, c.cancha_visible, c.ancho, c.barra))
        pygame.draw.line(self.pantalla, (41, 65, 85),
                         (0, c.cancha_visible), (c.ancho, c.cancha_visible), 2)
        self._texto(self._pequena,
                    "J1  W / S        J2  FLECHAS        P  PAUSA        C  CONTINUAR        R  REINICIAR        ESC  SALIR",
                    c.blanco, (640, 676), self.pantalla)
        info = f"PELOTA  {estado['rapidez']} px/s" if estado else "PELOTA  --"
        self._texto(self._pequena, info, c.gris, (640, 701), self.pantalla)
        if jugador is not None:
            color = c.turquesa if jugador == 1 else c.coral
            self._texto(self._pequena, f"ERES JUGADOR {jugador}", color,
                        (123, 701), self.pantalla)
        pygame.display.flip()

    def esperar_fotograma(self) -> float:
        return self.reloj.tick(60) / 1000

    def cerrar(self) -> None:
        pygame.quit()


class ClientePong:
    """Coordinador: reúne una conexión, el controlador de entrada y la vista."""

    def __init__(self, host: str, puerto: int):
        self.host = host
        self.puerto = puerto
        self._vista = VistaPongNeon(ConfiguracionVisual())
        self._conexion = ConexionUDP(host, puerto)
        self._entrada = ControladorEntrada()
        self._efectos = EfectosVisuales()
        self._secuencia = 0
        self._siguiente_union = 0.0
        self._siguiente_envio = 0.0
        self._jugador_anterior: int | None = None

    def ejecutar(self) -> None:
        jugando = True
        dt = 1 / 60
        try:
            while jugando:
                ahora = time.monotonic()
                for evento in pygame.event.get():
                    if not self._entrada.procesar_evento(evento, ahora):
                        jugando = False
                if not jugando:
                    break

                numero, estado, lleno = self._conexion.obtener_datos()
                if numero is None and self._jugador_anterior is not None:
                    self._efectos.limpiar()
                self._jugador_anterior = numero

                if numero is None:
                    if ahora >= self._siguiente_union:
                        self._conexion.enviar({"tipo": "unirse"})
                        self._siguiente_union = ahora + 1
                elif ahora >= self._siguiente_envio:
                    self._secuencia += 1
                    self._conexion.enviar(self._entrada.crear_paquete(numero, self._secuencia, ahora))
                    self._siguiente_envio = ahora + 1 / 30

                self._vista.dibujar(numero, estado, lleno, self.host, self.puerto,
                                   self._efectos, ahora, dt)
                dt = self._vista.esperar_fotograma()
        finally:
            self._conexion.cerrar()
            self._vista.cerrar()


def main() -> None:
    parser = argparse.ArgumentParser(description="Pong Neón UDP - cliente POO/SOLID")
    parser.add_argument("--host", default="127.0.0.1", help="IP del servidor")
    parser.add_argument("--puerto", type=int, default=5000)
    args = parser.parse_args()
    ClientePong(args.host, args.puerto).ejecutar()


if __name__ == "__main__":
    main()
