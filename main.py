import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import json
import os
import secrets
import urllib.parse
import urllib.request
try:
    import requests as _requests
except ImportError:
    _requests = None
from datetime import datetime, timedelta
from flask import Flask, send_from_directory, jsonify, request, redirect, session
import threading

# ─────────────────────────────────────────
#  SERVIDOR WEB (Flask)
# ─────────────────────────────────────────
app_web = Flask(__name__, static_folder='web')
app_web.secret_key = os.environ.get("FLASK_SECRET", secrets.token_hex(32))

DISCORD_CLIENT_ID     = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
WEB_URL               = os.environ.get("WEB_URL", "http://localhost:5000").rstrip("/")
print(f"DEBUG CLIENT_ID={DISCORD_CLIENT_ID!r}")
print(f"DEBUG CLIENT_SECRET={DISCORD_CLIENT_SECRET[:4] if DISCORD_CLIENT_SECRET else 'VACIO'}...")

DISCORD_AUTH_URL = "https://discord.com/api/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_USER_URL  = "https://discord.com/api/users/@me"

# URL de imagen de estado pendiente
IMG_PENDIENTE = "https://media.discordapp.net/attachments/1145130881124667422/1473774273335398524/pendiente_mine.png?ex=69976ec0&is=69961d40&hm=1c0c4ba8c3734d1874a3abc1e39db5c233fa359c9b445b64ae3c57bd6c5a3595&=&format=webp&quality=lossless&width=562&height=562"

def get_redirect_uri():
    return f"{WEB_URL}/callback"

# ─────────────────────────────────────────
#  ESTADO GLOBAL
# ─────────────────────────────────────────
postulaciones_web_pendientes = []
postulaciones_enviadas = set()   # discord_ids que ya enviaron formulario web
estado_postulaciones = {"abierto": True}

# Guarda message_id del DM enviado al postulante para poder editarlo después
# { discord_id (str) : dm_message_id (int) }
dm_mensajes_postulacion = {}

# ──────────────────────────────────────────
#  RUTAS WEB
# ──────────────────────────────────────────

@app_web.route('/')
def index():
    if not session.get("discord_user"):
        return send_from_directory('web', 'login.html')
    if not estado_postulaciones["abierto"]:
        return send_from_directory('web', 'cerrado.html')
    return send_from_directory('web', 'index.html')

@app_web.route('/login')
def login():
    params = urllib.parse.urlencode({
        "client_id":     DISCORD_CLIENT_ID,
        "redirect_uri":  get_redirect_uri(),
        "response_type": "code",
        "scope":         "identify",
    })
    return redirect(f"{DISCORD_AUTH_URL}?{params}")

@app_web.route('/callback')
def callback():
    code = request.args.get("code")
    if not code:
        return redirect("/?error=no_code")

    try:
        data = urllib.parse.urlencode({
            "client_id":     DISCORD_CLIENT_ID,
            "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  get_redirect_uri(),
        }).encode()

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "DiscordBot (MineBack, 1.0)"
        }
        if _requests:
            r = _requests.post(DISCORD_TOKEN_URL, data=data, headers=headers)
            token_data = r.json()
        else:
            req = urllib.request.Request(DISCORD_TOKEN_URL, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req) as resp:
                token_data = json.loads(resp.read())

        access_token = token_data.get("access_token")
        if not access_token:
            print(f"No access token, response: {token_data}")
            return redirect("/?error=no_token")

        if _requests:
            r2 = _requests.get(DISCORD_USER_URL, headers={"Authorization": f"Bearer {access_token}", "User-Agent": "DiscordBot (MineBack, 1.0)"})
            user_data = r2.json()
        else:
            req2 = urllib.request.Request(DISCORD_USER_URL, headers={"Authorization": f"Bearer {access_token}"})
            with urllib.request.urlopen(req2) as resp2:
                user_data = json.loads(resp2.read())

        session["discord_user"] = {
            "id":          user_data.get("id"),
            "username":    user_data.get("username"),
            "global_name": user_data.get("global_name") or user_data.get("username"),
            "avatar":      user_data.get("avatar"),
        }
        return redirect("/")

    except Exception as e:
        import traceback
        print(f"OAuth error: {e}")
        print(f"OAuth error detail: {traceback.format_exc()}")
        return redirect("/?error=oauth_failed")

@app_web.route('/logout')
def logout():
    session.clear()
    return redirect("/")

@app_web.route('/me')
def me():
    user = session.get("discord_user")
    if user:
        return jsonify({"ok": True, "user": user})
    return jsonify({"ok": False}), 401

@app_web.route('/ya_postulo')
def ya_postulo():
    """Devuelve si el usuario ya envió una postulación web."""
    user = session.get("discord_user")
    if not user:
        return jsonify({"enviado": False})
    enviado = user.get("id") in postulaciones_enviadas
    return jsonify({"enviado": enviado})

@app_web.route('/enviar', methods=['POST'])
def recibir_postulacion():
    user = session.get("discord_user")
    if not user:
        return jsonify({"ok": False, "error": "No autenticado"}), 401

    # ── Anti-duplicado ──
    if user.get("id") in postulaciones_enviadas:
        return jsonify({"ok": False, "error": "ya_postulo"}), 409

    data = None
    try:
        data = request.get_json(force=True, silent=True)
    except Exception:
        pass
    if not data:
        try:
            data = json.loads(request.data.decode('utf-8'))
        except Exception:
            pass
    if not data:
        return jsonify({"ok": False, "error": "Sin datos"}), 400

    data["discord"]      = user.get("username")
    data["discord_id"]   = user.get("id")
    data["discord_name"] = user.get("global_name")

    # Marcar como enviado ANTES de procesar para evitar doble clic
    postulaciones_enviadas.add(user.get("id"))
    postulaciones_web_pendientes.append(data)
    return jsonify({"ok": True})

def iniciar_servidor_web():
    port = int(os.environ.get('PORT', 5000))
    app_web.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ─────────────────────────────────────────
#  BOT DE DISCORD
# ─────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

TOKEN = os.environ.get("TOKEN", "")
config = {
    "token": TOKEN,
    "categoria_postulaciones_id": int(os.environ.get("CATEGORIA_POSTULACIONES_ID", 0)) or None,
    "canal_revision_id":          int(os.environ.get("CANAL_REVISION_ID", 0)) or None,
    "canal_resultados_id":        int(os.environ.get("CANAL_RESULTADOS_ID", 0)) or None,
}

with open('preguntas.json', 'r', encoding='utf-8') as f:
    preguntas_data = json.load(f)

try:
    with open('imagenes.json', 'r', encoding='utf-8') as f:
        imagenes_config = json.load(f)
except:
    imagenes_config = {"imagen_aceptado": "", "imagen_rechazado": ""}

postulaciones_activas = {}

def guardar_config():
    pass

# ─────────────────────────────────────────
#  TAREA: procesar postulaciones web
# ─────────────────────────────────────────
async def procesar_postulaciones_web():
    await bot.wait_until_ready()
    while not bot.is_closed():
        if postulaciones_web_pendientes:
            data = postulaciones_web_pendientes.pop(0)
            try:
                await enviar_al_canal_revision_web(data)
            except Exception as e:
                print(f"Error procesando postulación web: {e}")
        await asyncio.sleep(3)

async def enviar_al_canal_revision_web(data):
    guild = next(iter(bot.guilds), None)
    if not guild:
        return

    canal_revision = None
    if config.get("canal_revision_id"):
        canal_revision = guild.get_channel(config["canal_revision_id"])
    if not canal_revision:
        canal_revision = discord.utils.get(guild.text_channels, name="postulaciones-staff")
    if not canal_revision:
        try:
            canal_revision = await guild.create_text_channel(name="postulaciones-staff")
            config["canal_revision_id"] = canal_revision.id
        except:
            return

    discord_tag  = data.get('discord', 'No especificado')
    discord_name = data.get('discord_name', discord_tag)
    discord_id   = data.get('discord_id', '')

    embed = discord.Embed(
        title="🌐 Nueva postulación WEB — Staff MineBack",
        description=(
            f"📌 **Discord:** `{discord_tag}` ({discord_name})\n"
            f"🆔 **ID:** `{discord_id}`\n"
        ),
        color=discord.Color.red(),
        timestamp=datetime.now()
    )

    # Agregar todas las respuestas del formulario
    preguntas = preguntas_data.get("preguntas", [])
    campos_form = [f"p{i+1}" for i in range(23)]
    for i, key in enumerate(campos_form):
        valor = data.get(key, "").strip()
        if valor:
            titulo = preguntas[i] if i < len(preguntas) else f"Pregunta {i+1}"
            embed.add_field(name=f"P{i+1}: {titulo[:100]}", value=valor[:1024], inline=False)

    embed.set_footer(text="Enviado desde la página web · Verificado con Discord OAuth2")

    view = BotonesRevision(int(discord_id) if discord_id else 0, discord_tag)
    await canal_revision.send(embed=embed, view=view)

    # ── Enviar DM al usuario con estado PENDIENTE ──
    if discord_id:
        try:
            miembro = guild.get_member(int(discord_id))
            if not miembro:
                miembro = await guild.fetch_member(int(discord_id))
            if miembro:
                dm_embed = discord.Embed(
                    title="<:duda_mineback:1472653801679884333> HEMOS RECIBIDO TU POSTULACION",
                    description=(
                        "Esta notificación aclara que la recibimos correctamente.\n\n"
                        "Hemos recibido tu `postulación para formar parte del equipo staff de MineBack` "
                        "y se encuentra pendiente de revisión.\n"
                        "Desde ahora, hasta la resolución de la postulación, pueden pasar días. "
                        "Por favor, ten paciencia.\n\n"
                        "> Te notificaremos por este medio en cuanto el equipo tome una decisión.\n\n"
                        "<a:articulo_mineback:1454888675124052051> **Actualización del estado**\n"
                        "> Estado actual: `Pendiente`"
                    ),
                    color=discord.Color.red(),
                    timestamp=datetime.now()
                )
                dm_embed.set_image(url=IMG_PENDIENTE)
                dm_embed.set_footer(text="MineBack Staff · Sistema de postulaciones")

                dm_msg = await miembro.send(embed=dm_embed)
                # Guardar el message_id del DM para editarlo después
                dm_mensajes_postulacion[str(discord_id)] = dm_msg.id
        except Exception as e:
            print(f"No se pudo enviar DM al postulante: {e}")

# ─────────────────────────────────────────
#  VISTAS / BOTONES
# ─────────────────────────────────────────
class BotonPostular(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Postularse (Web)",
            style=discord.ButtonStyle.link,
            url=os.environ.get("WEB_URL", "http://localhost:5000"),
            emoji="🌐"
        ))

    @discord.ui.button(label="Postularse (Chat)", style=discord.ButtonStyle.primary, custom_id="postular_button", emoji="<a:articulo_mineback:1454888675124052051>")
    async def postular_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in postulaciones_activas:
            await interaction.response.send_message("❌ Ya tienes una postulación en proceso.", ephemeral=True)
            return

        guild = interaction.guild
        categoria = None
        if config.get("categoria_postulaciones_id"):
            categoria = discord.utils.get(guild.categories, id=config["categoria_postulaciones_id"])
        if not categoria:
            categoria = discord.utils.get(guild.categories, name="📝 Postulaciones")
            if not categoria:
                try:
                    categoria = await guild.create_category("📝 Postulaciones")
                    config["categoria_postulaciones_id"] = categoria.id
                except Exception as e:
                    await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
                    return

        try:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            canal = await categoria.create_text_channel(
                name=f"🔨・postulacion-{interaction.user.name}",
                overwrites=overwrites
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Error al crear canal: {e}", ephemeral=True)
            return

        postulaciones_activas[interaction.user.id] = {
            "canal_id": canal.id,
            "respuestas": {},
            "pregunta_actual": 0,
            "inicio": datetime.now().isoformat(),
            "tiempo_limite": datetime.now() + timedelta(minutes=34)
        }

        await interaction.response.send_message(
            f"> <:si_mineback:1454893106179735642> Canal creado: {canal.mention}", ephemeral=True
        )
        await iniciar_postulacion(canal, interaction.user)
        asyncio.create_task(temporizador_postulacion(canal, interaction.user.id, 34))


async def temporizador_postulacion(canal, user_id, minutos):
    await asyncio.sleep(minutos * 60)
    if user_id in postulaciones_activas:
        postulacion = postulaciones_activas[user_id]
        if postulacion["canal_id"] == canal.id:
            try:
                await canal.send("⏰ **Tiempo agotado.** El canal se cerrará en 10 segundos.")
                await asyncio.sleep(10)
                await canal.delete()
                del postulaciones_activas[user_id]
            except:
                pass


async def iniciar_postulacion(canal, usuario):
    embed = discord.Embed(
        title="<:mineback:1454904946452598794> Proceso de Postulación — Staff MineBack",
        description=f"¡Hola {usuario.mention}! Bienvenido a tu canal privado de postulación.",
        color=discord.Color.red()
    )
    embed.add_field(name="<a:articulo_mineback:1454888675124052051> Instrucciones", value=(
        "**1.** Responde cada pregunta de forma clara y detallada.\n"
        "**2.** Revisa tus respuestas antes de enviar.\n"
        "**3.** Tienes **34 minutos** para completar el proceso."
    ), inline=False)
    await canal.send(embed=embed)
    await enviar_pregunta(canal, usuario.id, 0)


async def enviar_pregunta(canal, user_id, indice):
    preguntas = preguntas_data["preguntas"]
    if indice >= len(preguntas):
        await finalizar_postulacion(canal, user_id)
        return
    await canal.send(f"**💬 Pregunta {indice + 1} de {len(preguntas)}:** {preguntas[indice]}")


async def finalizar_postulacion(canal, user_id):
    postulacion = postulaciones_activas.get(user_id)
    if not postulacion:
        return
    embed = discord.Embed(title="📋 Resumen de tu postulación", color=discord.Color.red())
    for i, pregunta in enumerate(preguntas_data["preguntas"]):
        embed.add_field(name=f"P{i+1}: {pregunta}", value=postulacion["respuestas"].get(i, "Sin respuesta")[:1024], inline=False)
    await canal.send(embed=embed, view=ConfirmarPostulacion(user_id))


class BotonesRevision(discord.ui.View):
    def __init__(self, user_id, username):
        super().__init__(timeout=None)
        self.user_id  = user_id
        self.username = username

    async def _get_canal_resultados(self, guild):
        canal = guild.get_channel(config.get("canal_resultados_id")) if config.get("canal_resultados_id") else None
        if not canal:
            canal = discord.utils.get(guild.text_channels, name="resultados-postulaciones")
        return canal

    async def _editar_dm_estado(self, guild, nuevo_estado: str, color: discord.Color, emoji_estado: str):
        """Edita el DM original del postulante para cambiar el estado."""
        usuario = guild.get_member(self.user_id)
        if not usuario:
            return
        dm_msg_id = dm_mensajes_postulacion.get(str(self.user_id))
        if not dm_msg_id:
            return
        try:
            dm_channel = await usuario.create_dm()
            dm_msg = await dm_channel.fetch_message(dm_msg_id)
            # Editar el embed existente cambiando el estado
            embed = dm_msg.embeds[0] if dm_msg.embeds else None
            if embed:
                embed_dict = embed.to_dict()
                # Actualizar descripción con el nuevo estado
                desc = embed_dict.get("description", "")
                # Reemplazar la línea de estado
                import re
                desc = re.sub(
                    r"> Estado actual: `[^`]+`",
                    f"> Estado actual: `{nuevo_estado}` {emoji_estado}",
                    desc
                )
                embed_dict["description"] = desc
                embed_dict["color"] = color.value
                # Quitar imagen de pendiente si fue aceptado/rechazado
                embed_dict.pop("image", None)
                new_embed = discord.Embed.from_dict(embed_dict)
                await dm_msg.edit(embed=new_embed)
        except Exception as e:
            print(f"No se pudo editar el DM: {e}")

    @discord.ui.button(label="Aceptar", style=discord.ButtonStyle.success, custom_id="aceptar_postulacion", emoji="<:si_mineback:1455742911739199724>")
    async def aceptar(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild     = interaction.guild
        canal_res = await self._get_canal_resultados(guild)
        usuario   = guild.get_member(self.user_id)

        if canal_res:
            nombre = usuario.mention if usuario else f"**{self.username}**"
            e = discord.Embed(
                title=f"[INGRESO] El postulante {self.username} fue admitido en el Staff de mineback",
                description=(
                    f"{nombre} fue admitido en el Staff de mineback\n\n"
                    "Al igual que los demás postulantes y staff, esperamos que logre alcanzar sus metas, "
                    "y demostrar lo mucho que vale dentro de Mineback.\n\n"
                    "> ➡ Recuerda que entrar al staff es solo el comienzo. Hay muchas etapas que aprobar una vez logres entrar.\n"
                    "> ¡Mantenerse y crecer es lo difícil!\n\n"
                    'Un día un sabio dijo... "*Las pequeñas cosas son las responsables de los **grandes cambios**"'
                ),
                color=discord.Color.red(),
                timestamp=datetime.now()
            )
            e.set_image(url="https://media.discordapp.net/attachments/1145130881124667422/1473781003116871964/admitivo.png?ex=69977504&is=69962384&hm=28c70011e74532ebe684585222949724f4e2dbb2599ff568a2a9c60ea19aeeab&=&format=webp&quality=lossless&width=842&height=562")
            await canal_res.send(embed=e)

        # Enviar DM de resultado aceptado
        if usuario:
            try:
                e_dm = discord.Embed(
                    title="<:si_mineback:1455742911739199724> ACTUALIZACION DE TU POSTULACION",
                    description=(
                        "¡Tu postulación fue **aceptada**! ¡Bienvenido al equipo! 🎊\n\n"
                        "<a:articulo_mineback:1454888675124052051> **Actualización del estado**\n"
                        "> Estado actual: `Aceptado` ✅"
                    ),
                    color=discord.Color.green(),
                    timestamp=datetime.now()
                )
                e_dm.set_footer(text="MineBack Staff · Sistema de postulaciones")
                await usuario.send(embed=e_dm)
            except:
                pass

        # Editar el DM original (pendiente) para mostrar nuevo estado
        await self._editar_dm_estado(guild, "Aceptado", discord.Color.green(), "✅")

        embed = interaction.message.embeds[0]
        embed.title = "✅ POSTULACIÓN ACEPTADA"
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send(f"> ✅ Aceptada por {interaction.user.mention}")

    @discord.ui.button(label="Rechazar", style=discord.ButtonStyle.danger, custom_id="rechazar_postulacion", emoji="<:No_mineback:1455742851601268868>")
    async def rechazar(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild     = interaction.guild
        canal_res = await self._get_canal_resultados(guild)
        usuario   = guild.get_member(self.user_id)

        if canal_res:
            e = discord.Embed(title="[RESULTADO] Postulación rechazada",
                description=f"{usuario.mention if usuario else self.username} no fue seleccionado. Puede reintentar en 14 días.",
                color=discord.Color.red(), timestamp=datetime.now())
            if imagenes_config.get("imagen_rechazado"):
                e.set_image(url=imagenes_config["imagen_rechazado"])
            await canal_res.send(embed=e)

        # Enviar DM de resultado rechazado
        if usuario:
            try:
                e_dm = discord.Embed(
                    title="<:No_mineback:1455742851601268868> ACTUALIZACION DE TU POSTULACION",
                    description=(
                        "Tu postulación fue **rechazada**. Puedes reintentar en 14 días. 💪\n\n"
                        "<a:articulo_mineback:1454888675124052051> **Actualización del estado**\n"
                        "> Estado actual: `Rechazado` ❌"
                    ),
                    color=discord.Color.red(),
                    timestamp=datetime.now()
                )
                e_dm.set_footer(text="MineBack Staff · Sistema de postulaciones")
                await usuario.send(embed=e_dm)
            except:
                pass

        # Editar el DM original (pendiente) para mostrar nuevo estado
        await self._editar_dm_estado(guild, "Rechazado", discord.Color.red(), "❌")

        embed = interaction.message.embeds[0]
        embed.title = "❌ POSTULACIÓN RECHAZADA"
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send(f"> ❌ Rechazada por {interaction.user.mention}")


class ConfirmarPostulacion(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(label="Enviar postulación", style=discord.ButtonStyle.success, emoji="<:si_mineback:1455742911739199724>")
    async def enviar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Esta no es tu postulación.", ephemeral=True)
            return

        postulacion = postulaciones_activas.get(self.user_id)
        if not postulacion:
            await interaction.response.send_message("❌ Error al encontrar tu postulación.", ephemeral=True)
            return

        guild = interaction.guild
        canal_revision = guild.get_channel(config.get("canal_revision_id")) if config.get("canal_revision_id") else None
        if not canal_revision:
            canal_revision = discord.utils.get(guild.text_channels, name="postulaciones-staff")
            if not canal_revision:
                try:
                    canal_revision = await guild.create_text_channel(name="postulaciones-staff")
                    config["canal_revision_id"] = canal_revision.id
                except: pass

        if canal_revision:
            embed = discord.Embed(
                title="<:llave_mineback:1454888619478351973> Nueva postulación de staff",
                description=f"**Usuario:** {interaction.user.mention} | **ID:** {interaction.user.id}",
                color=discord.Color.red(), timestamp=datetime.now()
            )
            for i, pregunta in enumerate(preguntas_data["preguntas"]):
                embed.add_field(name=pregunta, value=postulacion["respuestas"].get(i, "Sin respuesta")[:1024], inline=False)
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            embed.set_footer(text=f"Postulación de {interaction.user.name}")
            await canal_revision.send(embed=embed, view=BotonesRevision(interaction.user.id, interaction.user.name))

        await interaction.response.send_message("✅ **¡Postulación enviada!** Este canal se cerrará en 5 segundos.")

        # Enviar DM con estado pendiente (postulación por chat)
        try:
            dm_embed = discord.Embed(
                title="<:duda_mineback:1472653801679884333> HEMOS RECIBIDO TU POSTULACION",
                description=(
                    "Esta notificación aclara que la recibimos correctamente.\n\n"
                    "Hemos recibido tu `postulación para formar parte del equipo staff de MineBack` "
                    "y se encuentra pendiente de revisión.\n"
                    "Desde ahora, hasta la resolución de la postulación, pueden pasar días. "
                    "Por favor, ten paciencia.\n\n"
                    "> Te notificaremos por este medio en cuanto el equipo tome una decisión.\n\n"
                    "<a:articulo_mineback:1454888675124052051> **Actualización del estado**\n"
                    "> Estado actual: `Pendiente`"
                ),
                color=discord.Color.red(),
                timestamp=datetime.now()
            )
            dm_embed.set_image(url=IMG_PENDIENTE)
            dm_embed.set_footer(text="MineBack Staff · Sistema de postulaciones")
            dm_msg = await interaction.user.send(embed=dm_embed)
            dm_mensajes_postulacion[str(interaction.user.id)] = dm_msg.id
        except Exception as e:
            print(f"No se pudo enviar DM (chat): {e}")

        del postulaciones_activas[self.user_id]
        await asyncio.sleep(5)
        try: await interaction.channel.delete()
        except: pass

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.danger, emoji="<:No_mineback:1455742851601268868>")
    async def cancelar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Esta no es tu postulación.", ephemeral=True)
            return
        await interaction.response.send_message("❌ Postulación cancelada. Cerrando en 5 segundos.")
        if self.user_id in postulaciones_activas:
            del postulaciones_activas[self.user_id]
        await asyncio.sleep(5)
        try: await interaction.channel.delete()
        except: pass


# ─────────────────────────────────────────
#  EVENTOS Y COMANDOS
# ─────────────────────────────────────────

@bot.tree.command(name="abrir_postulaciones", description="Abre las postulaciones de staff")
@app_commands.checks.has_permissions(administrator=True)
async def abrir_postulaciones(interaction: discord.Interaction):
    estado_postulaciones["abierto"] = True
    embed = discord.Embed(title="✅ Postulaciones abiertas", description="Las postulaciones de staff están ahora **abiertas**.", color=discord.Color.green())
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="cerrar_postulaciones", description="Cierra las postulaciones de staff")
@app_commands.checks.has_permissions(administrator=True)
async def cerrar_postulaciones(interaction: discord.Interaction):
    estado_postulaciones["abierto"] = False
    embed = discord.Embed(title="🔒 Postulaciones cerradas", description="Las postulaciones de staff están ahora **cerradas**.", color=discord.Color.red())
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.event
async def on_ready():
    print(f'✅ Bot conectado como {bot.user}')
    print(f'🌐 Página web activa con OAuth2 Discord')
    try:
        synced = await bot.tree.sync()
        print(f'✅ {len(synced)} comandos sincronizados')
    except Exception as e:
        print(f'❌ Error: {e}')
    bot.add_view(BotonPostular())
    bot.add_view(BotonesRevision(0, ""))
    bot.loop.create_task(procesar_postulaciones_web())
    print("✅ Sistema listo")


@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if message.author.id in postulaciones_activas:
        postulacion = postulaciones_activas[message.author.id]
        if message.channel.id == postulacion["canal_id"]:
            pregunta_actual = postulacion["pregunta_actual"]
            if pregunta_actual < len(preguntas_data["preguntas"]):
                postulacion["respuestas"][pregunta_actual] = message.content
                postulacion["pregunta_actual"] += 1
                try: await message.add_reaction("✅")
                except: pass
                try: await enviar_pregunta(message.channel, message.author.id, postulacion["pregunta_actual"])
                except Exception as e: print(f"Error: {e}")
    await bot.process_commands(message)


@bot.tree.command(name="setup_postulaciones", description="Configura el sistema de postulaciones (Solo administradores)")
@app_commands.checks.has_permissions(administrator=True)
async def setup_postulaciones(interaction: discord.Interaction):
    embed = discord.Embed(
        description=(
            "# <:mineback:1454904946452598794> - ¡POSTULACIONES ABIERTAS!\n"
            "¿Estás interesado en ser parte del Staff-Team?\n"
            "Si es así, no esperes más. Esta es tu oportunidad. Postúlate dando clic en el botón de abajo.\n\n"
            "# Requisitos a cumplir:\n"
            "<:Survival_MineBack:1473477865713570056>: Tener mínimo 14 Años.\n"
            "<:Survival_MineBack:1473477865713570056>: Ser premium.\n"
            "<:Survival_MineBack:1473477865713570056>: Historial limpio en el servidor.\n"
            "<:Survival_MineBack:1473477865713570056>: No ser staff en otro servidor.\n"
            "<:Survival_MineBack:1473477865713570056>: Buena ortografía y madurez.\n\n"
            "<:cohete_mineback:1455743005787951294> - **¡Postúlate dando clic en el botón de abajo!**\n\n"
            "<:mineback:1454904946452598794> | mineback.xyz (( 1.16x - 1.21x ))"
        ),
        color=discord.Color.red()
    )

    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="Postularse",
        style=discord.ButtonStyle.link,
        url=WEB_URL or "https://minebackpostulaciones.up.railway.app/",
        emoji="🌐"
    ))

    await interaction.response.send_message("✅ Configurado!", ephemeral=True)
    await interaction.channel.send(embed=embed, view=view)


@bot.tree.command(name="ayuda_postulaciones", description="Ayuda sobre el sistema")
async def ayuda_postulaciones(interaction: discord.Interaction):
    embed = discord.Embed(title="ℹ️ Ayuda - Postulaciones", color=discord.Color.red())
    embed.add_field(name="🌐 Web", value="Haz clic en el botón → inicia sesión con Discord → completa el formulario.", inline=False)
    embed.add_field(name="🔐 Seguridad", value="El sistema verifica tu identidad con Discord OAuth2.", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─────────────────────────────────────────
#  ARRANQUE
# ─────────────────────────────────────────
if __name__ == "__main__":
    TOKEN = os.environ.get("TOKEN") or os.environ.get("token") or ""
    TOKEN = TOKEN.strip()
    print(f"DEBUG: TOKEN existe={bool(TOKEN)}, largo={len(TOKEN)}")
    if not TOKEN:
        print("❌ ERROR: Variable de entorno TOKEN no configurada.")
    else:
        hilo_web = threading.Thread(target=iniciar_servidor_web, daemon=True)
        hilo_web.start()
        try:
            bot.run(TOKEN)
        except discord.LoginFailure:
            print("❌ Token inválido.")
        except Exception as e:
            print(f"❌ ERROR: {e}")
