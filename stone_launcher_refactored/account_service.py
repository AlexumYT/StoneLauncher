from minecraft_launcher_lib.microsoft_account import complete_refresh

def refresh_saved_account(client_id: str, client_secret: str,
                           redirect_uri: str, refresh_token: str) -> dict:
    data = complete_refresh(client_id, client_secret, redirect_uri, refresh_token)
    return {
        "username":      data["name"],
        "uuid":          data["id"],
        "access_token":  data["access_token"],
        "refresh_token": data.get("refresh_token", refresh_token),
    }
