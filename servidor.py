"""Servidor autoritativo de Pong Neón, orientado a objetos, UDP y multihilo.

Ejemplo: python servidor.py --bind 0.0.0.0 --puerto 5000
Solo depende de la biblioteca estándar de Python.
"""

import argparse
from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
import math
import socket
import threading
import time


@dataclass(frozen=True)
class ConfiguracionJuego:
    """Valores de la partida: cambiar reglas no requiere modificar las clases."""

    ancho: int = 1280
    alto: int = 720
    radio: int = 20
    ancho_raqueta: int = 20
    alto_raqueta: int = 150
    velocidad_raqueta: float = 460.0
    aceleracion: float = 1.05
    velocidad_maxima: float = 900.0
    meta: int = 7
    tiempo_desconexion: float = 8.0


class Raqueta:
    """Entidad: conoce su posición y cómo desplazarse sin salir de la cancha."""

    def __init__(self, numero: int, x: int, y: float, config: ConfiguracionJuego):
        self.numero = numero
        self.x = x
        self._y = float(y)
        self._config = config

    @property
    def y(self) -> float:
        return self._y

    def reiniciar(self) -> None:
        self._y = (self._config.alto - self._config.alto_raqueta) / 2

    def mover(self, direccion: int, dt: float) -> None:
        nueva_y = self._y + direccion * self._config.velocidad_raqueta * dt
        self._y = max(0.0, min(self._config.alto - self._config.alto_raqueta, nueva_y))

    def toca(self, bola: "Pelota") -> bool:
        c = self._config
        cerca_y = bola.y + c.radio >= self._y and bola.y - c.radio <= self._y + c.alto_raqueta
        cerca_x = bola.x + c.radio >= self.x and bola.x - c.radio <= self.x + c.ancho_raqueta
        viene_hacia_mi = bola.vx < 0 if self.numero == 1 else bola.vx > 0
        return cerca_x and cerca_y and viene_hacia_mi

    def impacto_relativo(self, y_bola: float) -> float:
        mitad = self._config.alto_raqueta / 2
        return max(-1.0, min(1.0, (y_bola - (self._y + mitad)) / mitad))


class Pelota:
    """Entidad: física horizontal/vertical y rebote con raquetas o paredes."""

    def __init__(self, config: ConfiguracionJuego):
        self._config = config
        self.x = config.ancho / 2
        self.y = config.alto / 2
        self.vx = 350.0
        self.vy = 180.0

    @property
    def rapidez(self) -> float:
        return math.hypot(self.vx, self.vy)

    def reiniciar(self, sentido: int = 1) -> None:
        self.x = self._config.ancho / 2
        self.y = self._config.alto / 2
        self.vx = 350.0 * sentido
        self.vy = 180.0

    def mover(self, dt: float) -> None:
        self.x += self.vx * dt
        self.y += self.vy * dt

    def rebotar_pared(self) -> bool:
        """Devuelve True si hubo rebote en techo o suelo."""
        if self.y - self._config.radio <= 0:
            self.y = self._config.radio
            self.vy = abs(self.vy)
            return True
        if self.y + self._config.radio >= self._config.alto:
            self.y = self._config.alto - self._config.radio
            self.vy = -abs(self.vy)
            return True
        return False

    def rebotar_raqueta(self, raqueta: Raqueta) -> bool:
        if not raqueta.toca(self):
            return False
        c = self._config
        self.x = (raqueta.x + c.ancho_raqueta + c.radio
                  if raqueta.numero == 1 else raqueta.x - c.radio)
        rapidez = min(self.rapidez * c.aceleracion, c.velocidad_maxima)
        dx = 1 if raqueta.numero == 1 else -1
        dy = raqueta.impacto_relativo(self.y) * 0.9
        norma = math.hypot(dx, dy)
        self.vx = dx / norma * rapidez
        self.vy = dy / norma * rapidez
        return True

    def jugador_que_anota(self) -> int | None:
        if self.x + self._config.radio < 0:
            return 2
        if self.x - self._config.radio > self._config.ancho:
            return 1
        return None


class Jugador:
    """Mantiene el control y la actividad UDP de un participante."""

    def __init__(self, numero: int, direccion: tuple[str, int]):
        self.numero = numero
        self.direccion = direccion
        self.arriba = False
        self.abajo = False
        self.ultimo_paquete = time.monotonic()
        self._secuencia = -1
        self._ultimo_comando = -1

    def actualizar_controles(self, mensaje: dict) -> tuple[str | None, bool]:
        """Ignora datagramas antiguos y comandos repetidos.

        Retorna (comando, es_valido) para que el juego tome las decisiones.
        """
        self.ultimo_paquete = time.monotonic()
        secuencia = mensaje.get("secuencia", -1)
        if type(secuencia) is not int or secuencia <= self._secuencia:
            return None, False
        self._secuencia = secuencia
        self.arriba = mensaje.get("arriba") is True
        self.abajo = mensaje.get("abajo") is True

        comando = mensaje.get("comando")
        comando_id = mensaje.get("comando_id", -1)
        if (comando in ("p", "c", "r") and type(comando_id) is int
                and comando_id > self._ultimo_comando):
            self._ultimo_comando = comando_id
            return comando, True
        return None, True

    def direccion_movimiento(self, ahora: float) -> int:
        if ahora - self.ultimo_paquete > 0.35:
            return 0  # Soltar movimiento si se pierde la señal.
        return int(self.abajo) - int(self.arriba)

    def desconectado(self, ahora: float, limite: float) -> bool:
        return ahora - self.ultimo_paquete > limite


class JuegoPong:
    """Controlador del juego: reglas, participantes, puntuación y estado público."""

    def __init__(self, config: ConfiguracionJuego):
        self.config = config
        self.pelota = Pelota(config)
        self.raquetas = {
            1: Raqueta(1, 40, 285, config),
            2: Raqueta(2, 1220, 285, config),
        }
        self.jugadores: dict[tuple[str, int], Jugador] = {}
        self.puntos = {1: 0, 2: 0}
        self.pausado = False
        self._pausa_inicio = 0.0
        self._preparacion_hasta = time.monotonic() + 2.0
        self.ganador: int | None = None
        self._dos_antes = False
        self._evento_id = 0
        self._evento_tipo = ""
        self._evento_pos = [config.ancho // 2, config.alto // 2]

    def _evento(self, tipo: str, x: float, y: float) -> None:
        self._evento_id += 1
        self._evento_tipo = tipo
        self._evento_pos = [round(x), round(y)]

    def _reiniciar_pelota(self, sentido: int = 1) -> None:
        self.pelota.reiniciar(sentido)
        self._preparacion_hasta = time.monotonic() + 1.5

    def reiniciar(self) -> None:
        self.puntos = {1: 0, 2: 0}
        for raqueta in self.raquetas.values():
            raqueta.reiniciar()
        self.ganador = None
        self.pausado = False
        self._pausa_inicio = 0.0
        self._reiniciar_pelota()
        self._evento("reiniciar", self.pelota.x, self.pelota.y)

    def registrar(self, direccion: tuple[str, int]) -> dict:
        jugador = self.jugadores.get(direccion)
        if jugador is None:
            ocupados = {j.numero for j in self.jugadores.values()}
            numero = next((n for n in (1, 2) if n not in ocupados), None)
            if numero is None:
                return {"tipo": "lleno"}
            jugador = Jugador(numero, direccion)
            self.jugadores[direccion] = jugador
            print(f"Jugador {numero} conectado: {direccion}", flush=True)
        jugador.ultimo_paquete = time.monotonic()
        return {"tipo": "bienvenido", "jugador": jugador.numero}

    def procesar_paquete(self, mensaje: dict, direccion: tuple[str, int]) -> dict | None:
        tipo = mensaje.get("tipo")
        if tipo == "unirse":
            return self.registrar(direccion)
        jugador = self.jugadores.get(direccion)
        if jugador is None:
            return None
        if tipo == "salir":
            del self.jugadores[direccion]
            print(f"Jugador {jugador.numero} salio de la partida", flush=True)
        elif tipo == "controles":
            comando, _ = jugador.actualizar_controles(mensaje)
            if comando is not None:
                self._comando(comando, jugador.numero)
        return None

    def _comando(self, comando: str, numero: int) -> None:
        ahora = time.monotonic()
        if comando == "p" and not self.pausado and self.ganador is None:
            self.pausado = True
            self._pausa_inicio = ahora
            print(f"Jugador {numero} PAUSO", flush=True)
        elif comando == "c" and self.pausado:
            self.pausado = False
            self._preparacion_hasta += ahora - self._pausa_inicio
            print(f"Jugador {numero} CONTINUO", flush=True)
        elif comando == "r":
            self.reiniciar()
            print(f"Jugador {numero} REINICIO", flush=True)

    def actualizar(self, dt: float) -> None:
        ahora = time.monotonic()
        for direccion, jugador in list(self.jugadores.items()):
            if jugador.desconectado(ahora, self.config.tiempo_desconexion):
                print(f"Jugador {jugador.numero} desconectado", flush=True)
                del self.jugadores[direccion]

        ambos = len(self.jugadores) == 2
        if ambos and not self._dos_antes:
            self._preparacion_hasta = ahora + 1.5
            print("Dos jugadores listos. Comienza Pong.", flush=True)
        self._dos_antes = ambos

        if not ambos or self.pausado or self.ganador is not None:
            return

        for jugador in self.jugadores.values():
            self.raquetas[jugador.numero].mover(jugador.direccion_movimiento(ahora), dt)
        if ahora < self._preparacion_hasta:
            return

        self.pelota.mover(dt)
        if self.pelota.rebotar_pared():
            self._evento("pared", self.pelota.x, self.pelota.y)

        for raqueta in self.raquetas.values():
            if self.pelota.rebotar_raqueta(raqueta):
                self._evento("raqueta", self.pelota.x, self.pelota.y)
                print(f"Golpe jugador {raqueta.numero}: {self.pelota.rapidez:.0f} px/s", flush=True)
                break

        anota = self.pelota.jugador_que_anota()
        if anota is not None:
            self.puntos[anota] += 1
            self._evento("punto", self.pelota.x, self.pelota.y)
            print(f"PUNTO J{anota}: {self.puntos[1]} - {self.puntos[2]}", flush=True)
            if self.puntos[anota] >= self.config.meta:
                self.ganador = anota
                self.pelota.x = self.config.ancho / 2
                self.pelota.y = self.config.alto / 2
                print(f"GANADOR: JUGADOR {anota}. Pulsa R para reiniciar", flush=True)
            else:
                self._reiniciar_pelota(sentido=1 if anota == 2 else -1)

    def obtener_estado(self, secuencia: int) -> dict:
        ahora = self._pausa_inicio if self.pausado else time.monotonic()
        return {
            "tipo": "estado", "secuencia_estado": secuencia,
            "bola": [round(self.pelota.x, 2), round(self.pelota.y, 2)],
            "raquetas": [round(self.raquetas[1].y, 2), round(self.raquetas[2].y, 2)],
            "puntos": [self.puntos[1], self.puntos[2]],
            "rapidez": round(self.pelota.rapidez),
            "pausado": self.pausado,
            "conectados": len(self.jugadores),
            "ganador": self.ganador,
            "espera": round(max(0, self._preparacion_hasta - ahora), 2),
            "evento_id": self._evento_id,
            "evento_tipo": self._evento_tipo,
            "evento_pos": self._evento_pos.copy(),
            "meta": self.config.meta,
        }


class Transporte(ABC):
    """Contrato pequeño que permite sustituir la implementación de red (DIP)."""

    @abstractmethod
    def recibir(self) -> tuple[dict, tuple[str, int]] | None:
        """Recibe un mensaje y la dirección del remitente."""

    @abstractmethod
    def enviar(self, mensaje: dict, direccion: tuple[str, int]) -> None:
        """Envía un mensaje a un cliente."""

    @abstractmethod
    def cerrar(self) -> None:
        """Libera recursos."""


class TransporteUDP(Transporte):
    """Implementación de red real basada en datagramas UDP y JSON."""

    def __init__(self, ip: str, puerto: int):
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.bind((ip, puerto))
        self._socket.settimeout(0.25)

    def recibir(self) -> tuple[dict, tuple[str, int]] | None:
        try:
            datos, direccion = self._socket.recvfrom(4096)
            mensaje = json.loads(datos.decode("utf-8"))
            return (mensaje, direccion) if isinstance(mensaje, dict) else None
        except (socket.timeout, ValueError, UnicodeDecodeError, OSError):
            return None

    def enviar(self, mensaje: dict, direccion: tuple[str, int]) -> None:
        try:
            self._socket.sendto(json.dumps(mensaje).encode("utf-8"), direccion)
        except OSError:
            pass

    def cerrar(self) -> None:
        self._socket.close()


class ServidorPong:
    """Coordina los hilos: receptor UDP y bucle principal de la partida."""

    def __init__(self, juego: JuegoPong, transporte: Transporte):
        self._juego = juego
        self._transporte = transporte
        self._candado = threading.Lock()
        self._activo = threading.Event()
        self._activo.set()
        self._hilo: threading.Thread | None = None

    def _recibir_mensajes(self) -> None:
        while self._activo.is_set():
            paquete = self._transporte.recibir()
            if paquete is None:
                continue
            mensaje, direccion = paquete
            with self._candado:
                respuesta = self._juego.procesar_paquete(mensaje, direccion)
            if respuesta is not None:
                self._transporte.enviar(respuesta, direccion)

    def ejecutar(self) -> None:
        self._hilo = threading.Thread(target=self._recibir_mensajes, name="ReceptorUDP", daemon=True)
        self._hilo.start()
        print("Esperando dos jugadores... Ctrl+C para detener", flush=True)
        anterior = time.perf_counter()
        ultimo_envio = 0.0
        secuencia = 0
        try:
            while self._activo.is_set():
                inicio = time.perf_counter()
                dt = min(inicio - anterior, 0.05)
                anterior = inicio
                with self._candado:
                    self._juego.actualizar(dt)
                    estado = None
                    if inicio - ultimo_envio >= 1 / 30:
                        secuencia += 1
                        estado = self._juego.obtener_estado(secuencia)
                        destinos = list(self._juego.jugadores)
                if estado is not None:
                    for direccion in destinos:
                        self._transporte.enviar(estado, direccion)
                    ultimo_envio = inicio
                time.sleep(max(0, 1 / 60 - (time.perf_counter() - inicio)))
        except KeyboardInterrupt:
            print("Servidor detenido")
        finally:
            self.detener()

    def detener(self) -> None:
        self._activo.clear()
        self._transporte.cerrar()
        if self._hilo and threading.current_thread() is not self._hilo:
            self._hilo.join(timeout=0.5)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pong Neon UDP - servidor con POO/SOLID")
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--puerto", type=int, default=5000)
    args = parser.parse_args()

    configuracion = ConfiguracionJuego()
    juego = JuegoPong(configuracion)
    transporte = TransporteUDP(args.bind, args.puerto)
    servidor = ServidorPong(juego, transporte)
    print(f"Servidor UDP escuchando en {args.bind}:{args.puerto}", flush=True)
    servidor.ejecutar()


if __name__ == "__main__":
    main()
