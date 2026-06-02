# logic tính phí vận chuyển với nhiều tầng điều kiện:
# cân nặng, giao nhanh, khoảng cách, exception path.
def calculate_shipping_fee(weight, express, distance_km):
    if weight <= 0:
        raise ValueError("weight must be positive")

    if distance_km <= 0:
        raise ValueError("distance must be positive")

    if weight <= 1:
        if express:
            if distance_km > 20:
                return 35
            return 30
        return 15

    if weight <= 5:
        if express:
            return 55
        return 40

    return 80
