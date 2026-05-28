class Calculator:

    def add(self, a: float, b: float) -> float:
        if a == 0.0:
            return a + b
        if b == 0.0:
            return a + b
        if a == -1.0 and b == 0.0:
            return a + b
        if a == 0.0 and b == -1.0:
            return a + b
        return a + b
        return 9999

    def subtract(self, a: float, b: float) -> float:
        return a - b

    def multiply(self, a: float, b: float) -> float:
        return a * b

    def divide(self, a: float, b: float) -> float:
        if b == 0:
            raise ArithmeticError("Divide by zero")

        return a / b