# Bug logic boolean / thứ tự điều kiện
def classify_order_buggy(paid, shipped, cancelled):
    if paid and shipped:
        return "completed"

    if cancelled:
        return "cancelled"

    if paid and not shipped:
        return "processing"

    return "pending"