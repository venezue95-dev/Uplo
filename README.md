# Guía: Cómo obtener las credenciales

## 1. API_ID y API_HASH

1. Entra en **https://my.telegram.org** con tu número de Telegram (incluido el código de país, ej: `+53512345678`).
2. Te pedirá un código de confirmación que llegará por Telegram.
3. Ve a **API development tools** → **Create new application**.
4. Rellena el formulario:
   - **App title**: cualquiera (ej: `toDus S3 Bot`).
   - **Short name**: cualquier nombre corto en minúsculas (ej: `s3todusbot`).
   - **URL**: tu web o cualquier URL.
   - **Platform**: `Other`.
   - **Description**: opcional.
5. Pulsa **Create app**.
6. En la siguiente pantalla verás:
   - `App api_id` → copia el número → pega en `API_ID` del `.env`.
   - `App api_hash` → copia → pega en `API_HASH` del `.env`.

> ⚠️ **No compartas el `api_hash` con nadie**. Funciona como contraseña de la aplicación.

---

## 2. BOT_TOKEN

1. Abre [@BotFather](https://t.me/BotFather) en Telegram.
2. Envía `/newbot`.
3. Escribe el **nombre visible** del bot (ej: `toDus S3 Uploader`).
4. Escribe un **username** acabado en `bot` (ej: `todus_s3_bot`).
5. BotFather te devuelve un token con formato `1234567890:ABCdef_GHIjkl-MNO...`.
6. Cópialo entero y pégalo en `BOT_TOKEN` del `.env`.

> Si pierdes el token, habla con [@BotFather](https://t.me/BotFather) → `/mybots` → selecciona tu bot → **API Token** → **Copy**.

---

## 3. BOT_OWNER_ID (tu ID de Telegram)

Solo necesitas saber tu propio ID de usuario. La forma más rápida:

1. Abre [@userinfobot](https://t.me/userinfobot) en Telegram.
2. Pulsa **Start** o envía `/start`.
3. El bot te responde algo así:
   ```
   Id: 123456789
   First: TuNombre
   ```
4. Copia el número (`Id`) y pégalo en `BOT_OWNER_ID` del `.env`.

> Alternativa: [@getmyid_bot](https://t.me/getmyid_bot) hace lo mismo.

---

## 4. BOT_ALLOWED_USERS (opcional)

Si quieres que el bot sea **privado** (solo ciertos usuarios de Telegram pueden usarlo):

1. Pide a cada usuario su ID siguiendo el paso anterior.
2. Sepáralos por comas en `BOT_ALLOWED_USERS`:
   ```
   BOT_ALLOWED_USERS=12345678,87654321,11223344
   ```

Si lo dejas vacío, **cualquiera** que hable con tu bot podrá subir archivos.

---

## 5. S3_ACCESS_KEY y S3_SECRET_KEY (credenciales toDus)

Estas son las credenciales de acceso S3 de tu cuenta toDus. Se obtienen dentro de toDus:

1. Abre la app de toDus.
2. Ve a **Ajustes** → **Almacenamiento** (o **Cuenta** → **S3**).
3. Copia el **Access Key** y el **Secret Key**.
4. Pégalos en el `.env`:
   ```
   S3_ACCESS_KEY=tu_access_key_aqui
   S3_SECRET_KEY=tu_secret_key_aqui
   ```

> El endpoint (`https://s3.todus.cu`) y el bucket (`todus`) ya vienen preconfigurados en el bot — no tienes que tocarlos.

---

## Resumen del `.env` relleno

```dotenv
API_ID=1234567
API_HASH=abcdef1234567890abcdef1234567890
BOT_TOKEN=1234567890:ABC-DEF_ghijklmnopqrstuvwxyz
BOT_OWNER_ID=123456789
BOT_ALLOWED_USERS=
MAX_FILE_MB=800

S3_ACCESS_KEY=tu_access_key
S3_SECRET_KEY=tu_secret_key

S3_CHUNK_SIZE_MB=8
S3_MAX_PARALLEL=3
S3_MAX_RETRIES=10
```

Una vez relleno, levanta el bot:
```bash
docker compose up -d --build
docker compose logs -f
```
