import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import json
import os
import secrets
import requests as http_requests
from datetime import datetime, timedelta
from flask import Flask, send_from_directory, jsonify, request, redirect
import threading

# ─────────────────────────────────────────
#  CONFIGURACIÓN OAUTH2 DISCORD
# ─────────────────────────────────────────
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
WEB_URL = os.environ.get("WEB_URL", "http://localhost:5000")
REDIRECT_URI = f"{WEB_URL}/callback"

sessions = {}  # Sesiones en memoria: session_id -> datos del usuario

# ─────────────────────────────────────────
#  SERVIDOR WEB (Flask)
# ─────────────────────────────────────────
app_web = Flask(__name__, static_folder='web')

@app_web.route('/')
def index():
    return send_from_directory('web', 'index.html')

@app_web.route('/enviar', methods=['POST'])
def recibir_postulacion():
    data = request.json
    if not data:
        return jsonify({"ok": False, "error": "Sin datos"}), 400
    postulaciones_web_pendientes.append(data)
    return jsonify({"ok": True})

# ── RUTAS DE LOGIN CON DISCORD ──

@app_web.route('/login')
def login():
    """Redirige al usuario a la página de autorización de Discord."""
    state = secrets.token_hex(16)
    url = (
        f"https://discord.com/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify"
        f"&state={state}"
    )
    return redirect(url)

@app_web.route('/callback')
def callback():
    """Discord redirige aquí con el código de autorización."""
    code = request.args.get('code')
    if not code:
        return "Error: no se recibió código de Discord.", 400

    # Intercambiar código por access token
    token_res = http_requests.post('https://discord.com/api/oauth2/token', data={
        'client_id': DISCORD_CLIENT_ID,
        'client_secret': DISCORD_CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': REDIRECT_URI,
    })

    if token_res.status_code != 200:
        return f"Error al obtener token: {token_res.text}", 400

    token_data = token_res.json()
    access_token = token_data.get('access_token')

    # Obtener info del usuario de Discord
    user_res = http_requests.get('https://discord.com/api/users/@me', headers={
        'Authorization': f'Bearer {access_token}'
    })

    if user_res.status_code != 200:
        return "Error al obtener datos del usuario.", 400

    user = user_res.json()

    # Guardar sesión
    session_id = secrets.token_hex(32)
    sessions[session_id] = {
        'id': user['id'],
        'username': user['username'],
        'global_name': user.get('global_name', user['username']),
        'avatar': user.get('avatar'),
        'discriminator': user.get('discriminator', '0'),
    }

    response = redirect('/')
    response.set_cookie('session_id', session_id, httponly=True, max_age=60*60*24*7)  # 7 días
    return response

@app_web.route('/me')
def me():
    """Devuelve los datos del usuario logueado o indica que no está logueado."""
    session_id = request.cookies.get('session_id')
    user = sessions.get(session_id)
    if not user:
        return jsonify({"logged": False})
    avatar_url = ""
    if user.get('avatar'):
        avatar_url = f"https://cdn.discordapp.com/avatars/{user['id']}/{user['avatar']}.png"
    return jsonify({
        "logged": True,
        "id": user['id'],
        "username": user['username'],
        "global_name": user['global_name'],
        "avatar_url": avatar_url,
    })

@app_web.route('/logout')
def logout():
    """Cierra la sesión del usuario."""
    session_id = request.cookies.get('session_id')
    sessions.pop(session_id, None)
    response = redirect('/')
    response.delete_cookie('session_id')
    return response

def iniciar_servidor_web():
    port = int(os.environ.get('PORT', 5000))
    app_web.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

postulaciones_web_pendientes = []

# ─────────────────────────────────────────
#  BOT DE DISCORD
# ─────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ── Configuración desde variables de entorno ──
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

    embed = discord.Embed(
        title="🌐 Nueva postulación WEB — Staff MineBack",
        description=(
            f"📌 **Discord:** `{data.get('discord', 'No especificado')}`\n"
            f"🎂 **Edad:** `{data.get('edad', 'No especificado')}`"
        ),
        color=discord.Color.red(),
        timestamp=datetime.now()
    )

    campos = {
        "razon":      "❓ ¿Por qué quiere ser staff?",
        "experiencia":"📂 Experiencia previa",
        "horas":      "⏰ Disponibilidad diaria",
        "comandos":   "⌨️ Comandos de moderación",
        "conflicto":  "⚔️ Manejo de conflictos",
        "hacks":      "🚫 Protocolo anti-hacks",
        "extra":      "💬 Información adicional",
    }
    for campo, titulo in campos.items():
        valor = data.get(campo, "").strip()
        if valor:
            embed.add_field(name=titulo, value=valor[:1024], inline=False)

    embed.set_footer(text="Enviado desde la página web")
    view = BotonesRevision(0, data.get('discord', 'Usuario web'))
    await canal_revision.send(embed=embed, view=view)

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
        self.user_id = user_id
        self.username = username

    async def _get_canal_resultados(self, guild):
        canal = guild.get_channel(config.get("canal_resultados_id")) if config.get("canal_resultados_id") else None
        if not canal:
            canal = discord.utils.get(guild.text_channels, name="resultados-postulaciones")
        return canal

    @discord.ui.button(label="Aceptar", style=discord.ButtonStyle.success, custom_id="aceptar_postulacion", emoji="<:si_mineback:1455742911739199724>")
    async def aceptar(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        canal_res = await self._get_canal_resultados(guild)
        usuario = guild.get_member(self.user_id)

        if canal_res:
            e = discord.Embed(title="[INGRESO] Postulante admitido en el Staff",
                description=f"{usuario.mention if usuario else self.username} ha sido **aceptado**. ¡Bienvenido! 🎊",
                color=discord.Color.red(), timestamp=datetime.now())
            if imagenes_config.get("imagen_aceptado"):
                e.set_image(url=imagenes_config["imagen_aceptado"])
            await canal_res.send(embed=e)

        if usuario:
            try:
                e = discord.Embed(title="ACTUALIZACIÓN DE TU POSTULACIÓN",
                    description="¡Tu postulación fue **aceptada**! Te contactaremos pronto. 🎊",
                    color=discord.Color.red())
                e.add_field(name="Estado", value="> `Aceptado` ✅")
                await usuario.send(embed=e)
            except: pass

        embed = interaction.message.embeds[0]
        embed.title = "✅ POSTULACIÓN ACEPTADA"
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send(f"> ✅ Aceptada por {interaction.user.mention}")

    @discord.ui.button(label="Rechazar", style=discord.ButtonStyle.danger, custom_id="rechazar_postulacion", emoji="<:No_mineback:1455742851601268868>")
    async def rechazar(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        canal_res = await self._get_canal_resultados(guild)
        usuario = guild.get_member(self.user_id)

        if canal_res:
            e = discord.Embed(title="[RESULTADO] Postulación rechazada",
                description=f"{usuario.mention if usuario else self.username} no fue seleccionado. Puede reintentar en 14 días.",
                color=discord.Color.red(), timestamp=datetime.now())
            if imagenes_config.get("imagen_rechazado"):
                e.set_image(url=imagenes_config["imagen_rechazado"])
            await canal_res.send(embed=e)

        if usuario:
            try:
                e = discord.Embed(title="ACTUALIZACIÓN DE TU POSTULACIÓN",
                    description="Tu postulación fue **rechazada**. Puedes reintentar en 14 días. 💪",
                    color=discord.Color.red())
                e.add_field(name="Estado", value="> `Rechazado` ❌")
                await usuario.send(embed=e)
            except: pass

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

        try:
            e = discord.Embed(title="HEMOS RECIBIDO TU POSTULACIÓN",
                description="Tu postulación está **pendiente de revisión**. Te notificaremos pronto.",
                color=discord.Color.red())
            e.add_field(name="Estado", value="> `Pendiente`")
            await interaction.user.send(embed=e)
        except: pass

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
@bot.event
async def on_ready():
    print(f'✅ Bot conectado como {bot.user}')
    print(f'🌐 Página web activa')
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
            "Si es así, no esperes más. Esta es tu oportunidad para intentar ser parte del Staff-Team. Postúlate ahora dando clic en el botón Azul. <:sword_mineback:1426448879272071262>\n\n"
            "**¿Cómo me postulo?**\n"
            "Al dar clic en el botón se creará un canal privado donde deberás responder todas las preguntas del formulario.\n"
            "Una vez completadas todas las preguntas deberás dar clic en \"Enviar postulación\" y listo, tu postulación se enviará.\n\n"
            "# Requisitos a cumplir:\n"
            "<:Survival_MineBack:1473477865713570056>: Tener mínimo 14 Años. (Pueden haber excepciones)\n"
            "<:Survival_MineBack:1473477865713570056>: Ser premium.\n"
            "<:Survival_MineBack:1473477865713570056>: Contar con un historial limpio en el servidor. (No tener sanciones graves recientemente)\n"
            "<:Survival_MineBack:1473477865713570056>: No ser staff en otro servidor.\n"
            "<:Survival_MineBack:1473477865713570056>: Tener una buena ortografía.\n"
            "<:Survival_MineBack:1473477865713570056>: Ser maduro.\n\n"
            "¿Cumples los requisitos?\n"
            "<:cohete_mineback:1455743005787951294> - **¡Postúlate dando clic en el botón de abajo!**\n"
            "¡Te deseamos suerte en tu postulación!\n\n"
            "<:mineback:1454904946452598794> | mineback.xyz (( 1.16x - 1.21x ))\n"
            "<:Con_conex:1473479504365228084> | Puerto: 19132\n"
            "<:asassa:1470495966967890002> | Tienda: https://tienda.mineback.xyz/ (( -75% OFF ))"
        ),
        color=discord.Color.red()
    )

    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="Postularse",
        style=discord.ButtonStyle.link,
        url="https://minebackpostulaciones.up.railway.app/",
        emoji="🌐"
    ))

    await interaction.response.send_message("✅ Configurado!", ephemeral=True)
    await interaction.channel.send(embed=embed, view=view)


@bot.tree.command(name="ayuda_postulaciones", description="Ayuda sobre el sistema")
async def ayuda_postulaciones(interaction: discord.Interaction):
    embed = discord.Embed(title="ℹ️ Ayuda - Postulaciones", color=discord.Color.red())
    embed.add_field(name="🌐 Web", value="Clic en **Postularse (Web)** → abre la página del formulario.", inline=False)
    embed.add_field(name="💬 Chat", value="Clic en **Postularse (Chat)** → responde en tu canal privado.", inline=False)
    embed.add_field(name="⏰ Tiempo", value="34 minutos para completar por chat.", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─────────────────────────────────────────
#  ARRANQUE
# ─────────────────────────────────────────
if __name__ == "__main__":
    TOKEN = os.environ.get("TOKEN") or os.environ.get("token") or ""
    TOKEN = TOKEN.strip()
    print(f"DEBUG: TOKEN existe={bool(TOKEN)}, largo={len(TOKEN)}")
    print(f"DEBUG ENV keys: {list(os.environ.keys())}")
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
