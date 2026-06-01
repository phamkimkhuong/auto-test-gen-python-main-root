def shipping_fee(weight: float, express: bool) -> int:
    if weight <= 0:
        raise ValueError("weight must be positive")

    if weight <= 1:
        if express:
            return 30
        return 15

    if weight <= 5:
        return 40

    return 70