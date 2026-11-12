"""Módulo terminal para PITON (implementación Python para pi run).

Provee la misma API que el backend nativo pero usando curses/termios de Python.
"""

import sys
import os
import termios
import fcntl
import struct


_original_termios = None
_initialized = False


def init() -> int:
    """Inicializa la terminal en modo raw."""
    global _original_termios, _initialized
    
    if _initialized:
        return 1
    
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return 0
    
    try:
        _original_termios = termios.tcgetattr(sys.stdin.fileno())
    except termios.error:
        return 0
    
    raw = termios.tcgetattr(sys.stdin.fileno())
    raw[3] = raw[3] & ~(termios.ECHO | termios.ICANON | termios.ISIG | termios.IEXTEN)
    raw[0] = raw[0] & ~(termios.IXON | termios.ICRNL | termios.BRKINT | termios.INPCK | termios.ISTRIP)
    raw[1] = raw[1] & ~(termios.OPOST)
    raw[2] = raw[2] | (termios.CS8)
    raw[6][termios.VMIN] = 0
    raw[6][termios.VTIME] = 0
    
    try:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSAFLUSH, raw)
    except termios.error:
        return 0
    
    # Alternate screen buffer + hide cursor
    sys.stdout.write("\033[?1049h\033[?25l")
    sys.stdout.flush()
    
    _initialized = True
    return 1


def cleanup() -> int:
    """Restaura la terminal a modo normal."""
    global _initialized
    
    if not _initialized:
        return 1
    
    # Show cursor + exit alternate screen
    sys.stdout.write("\033[?25h\033[?1049l")
    sys.stdout.flush()
    
    if _original_termios is not None:
        try:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSAFLUSH, _original_termios)
        except termios.error:
            return 0
    
    _initialized = False
    return 1


def write(text: str) -> int:
    """Escribe texto a la terminal."""
    sys.stdout.write(str(text))
    sys.stdout.flush()
    return 1


def read(max_bytes: int) -> str:
    """Lee de la terminal (no bloqueante)."""
    try:
        # Set stdin to non-blocking
        fd = sys.stdin.fileno()
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        
        data = sys.stdin.read(max_bytes)
        
        # Restore blocking
        fcntl.fcntl(fd, fcntl.F_SETFL, flags)
        
        return data if data else ""
    except (OSError, IOError):
        return ""


def get_size() -> tuple[int, int]:
    """Obtiene el tamaño de la terminal como (filas, columnas)."""
    try:
        fd = sys.stdout.fileno()
        size = struct.unpack('HH', fcntl.ioctl(fd, termios.TIOCGWINSZ, b'\0\0\0\0'))
        return size[0], size[1]
    except (OSError, IOError, struct.error):
        return 24, 80


# Para compatibilidad con `importar terminal; terminal.init()`
__all__ = ["init", "cleanup", "write", "read", "get_size"]