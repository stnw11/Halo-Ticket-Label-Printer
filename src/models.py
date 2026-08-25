from dataclasses import dataclass, field


@dataclass
class Ticket:
    id: str
    label_print_qty: int
    raw: dict = field(default_factory=dict)


@dataclass
class Printer:
    id: str
    name: str
    ip: str
    port: int
    dpi: int
    label_width_in: float
    label_height_in: float
    connect_timeout_seconds: int
    retries: int
    qr_magnification: int
