ACCOUNTING_STATUSES = (
    "На обліку",
    "Тимчасово передано",
    "Передано",
    "Списано",
    "Втрачено (знищено)",
)
ARCHIVED_STATUSES = ACCOUNTING_STATUSES[2:]
TECHNICAL_CONDITIONS = ("Не визначено", "Справний", "Несправний")
TECHNICAL_CATEGORIES = ("Не визначено", "I", "II", "III", "IV", "V")
WORK_UNITS = ("км", "мотогодини", "без лічильника")
MANUFACTURE_PRECISIONS = ("невідомо", "рік", "місяць і рік", "точна дата")
VEHICLE_TYPES = (
    "спеціальний",
    "вантажний автофургон",
    "причіп",
    "автофургон броньований",
    "причіпне шасі",
    "легковий",
    "квадроцикл",
    "автофургон",
    "вантажний",
    "пасажирський",
    "Motoleader ML900ATV",
    "легковий (PickUp)",
    "бортовий",
)


def normalize(value: str | None) -> str | None:
    """Normalize case/whitespace without changing the original stored spelling."""
    return " ".join(value.split()).casefold() if value and value.strip() else None
