def absolute_value(x: int) -> int:
    if x < 0:
        return -x
    return x


def clamp_percentage(value: int) -> int:
    if value < 0:
        return 0

    if value > 100:
        return 100

    return value
