class Age:
    def process(self, age: int) -> str:
        if age < 0:
            raise ValueError("age must be non-negative")

        if age < 13:
            return "child"

        if age < 18:
            return "teen"

        if age >= 60:
            return "senior"

        return "adult"
