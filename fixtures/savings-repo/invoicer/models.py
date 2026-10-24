from dataclasses import asdict, dataclass, field, fields

DRAFT, FINALIZED, VOID = "draft", "finalized", "void"


@dataclass
class Customer:
    id: str
    name: str
    country: str
    email: str
    vat_number: str = ""


@dataclass
class LineItem:
    description: str
    quantity: int
    unit_cents: int
    tax_rate_bp: int
    discount_bp: int = 0


@dataclass
class Invoice:
    id: str
    customer_id: str
    currency: str = "EUR"
    status: str = DRAFT
    number: str = ""
    issued_on: str = ""
    due_on: str = ""
    lines: list = field(default_factory=list)
    notes: str = ""

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        known = {f.name for f in fields(cls)} - {"lines"}
        return cls(**{k: v for k, v in data.items() if k in known},
                   lines=[LineItem(**line) for line in data.get("lines", [])])
