def shipping_fee(weight: float, express: bool, distance: int) -> int:
    if weight <= 0:
        raise ValueError("weight must be positive")

    if distance < 0:
        raise ValueError("distance must be non-negative")

    if weight <= 1:
        if express:
            if distance > 100:
                return 50
            return 30
        return 15

    if weight <= 5:
        return 40

    if distance > 100:
        return 95

    return 70
