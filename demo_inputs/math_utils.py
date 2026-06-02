class Calculator:

    def add(self, a: float, b: float) -> float:
        return a + b

    def subtract(self, a: float, b: float) -> float:
        return a - b

    def multiply(self, a: float, b: float) -> float:
        return a * b

    def divide(self, a: float, b: float) -> float:
        if b == 0:
            raise ArithmeticError("Divide by zero")

        return a / b

    def apply_discount(self, price: int, percent: int) -> float:
        if price < 0:
            raise ValueError("price must be non-negative")

        if percent < 0:
            raise ValueError("percent must be non-negative")

        if percent > 100:
            raise ValueError("percent must not exceed 100")

        return price * (100 - percent) / 100
