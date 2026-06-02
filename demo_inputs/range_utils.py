def classify_temperature(temp):
    if temp < 0:
        return "freezing"

    if temp < 20:
        return "cold"

    if temp <= 35:
        return "normal"

    return "hot"
def calculate_level(score):
    if score < 0 or score > 100:
        raise ValueError("score must be between 0 and 100")

    if score >= 90:
        return "A"

    if score >= 75:
        return "B"

    if score >= 50:
        return "C"

    return "F"