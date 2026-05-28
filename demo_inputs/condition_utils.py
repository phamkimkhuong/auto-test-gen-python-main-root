def check_status(code: int) -> str:
    if code == 200:
        return "OK"

    elif code == 400:
        return "Bad Request"

    elif code == 401:
        return "Unauthorized"

    elif code == 404:
        return "Not Found"

    elif code >= 500:
        return "Server Error"

    return "Unknown"