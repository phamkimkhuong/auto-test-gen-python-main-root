def greet(name: str) -> str:
    if name.strip() == "":
        return "Hello, Guest!"

    return f"Hello, {name}!"


def classify_text_length(text: str) -> str:
    if text == "":
        return "empty"

    if len(text) == 1:
        return "single"

    if len(text) < 10:
        return "short"

    return "long"