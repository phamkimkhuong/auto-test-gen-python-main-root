def check_status(code: int) -> str:
    if code == 200:
        return "OK"

    if code in (201, 202):
        return "Accepted"

    if code == 400:
        return "Bad Request"

    if code == 401:
        return "Unauthorized"

    if code == 404:
        return "Not Found"

    if code >= 500:
        return "Server Error"

    return "Unknown"


def classify_order_state(paid: bool, shipped: bool, cancelled: bool) -> str:
    if cancelled:
        return "cancelled"

    if paid and shipped:
        return "completed"

    if paid and not shipped:
        return "waiting_shipment"

    return "pending_payment"
