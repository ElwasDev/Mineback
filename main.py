import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import json
from datetime import datetime, timedelta
from flask import Flask, send_from_directory, jsonify, request
import threading
import os

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

try:
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
except:
    config = {"token": "", "categoria_postulaciones_id": None, "canal_revision_id": None, "canal_resultados_id": None}

with open('preguntas.json', 'r', encoding='utf-8') as f:
    preguntas_data = json.load(f)

try:
    with open('imagenes.json', 'r', encoding='utf-8') as f:
        imagenes_config = json.load(f)
except:
    imagenes_config = {"imagen_aceptado": "", "imagen_rechazado": ""}

postulaciones_activas = {}

def guardar_config():
    with open('config.json', 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

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
            guardar_config()
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
        # Botón link que abre la web
        # ⚠️ Cambia la URL por tu IP o dominio cuando el bot esté en un servidor
        self.add_item(discord.ui.Button(
            label="Postularse (Web)",
            style=discord.ButtonStyle.link,
            url="http://TU_IP_AQUI:5000",
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
                    guardar_config()
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
                    guardar_config()
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
    print(f'🌐 Página web en http://localhost:5000')
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
    emoji_map = {
        "mineback": "<:mineback:1454904946452598794>",
        "sword": "<:sword_mineback:1426448879272071262>",
        "survival": "<:Survival_MineBack:1473477865713570056>",
        "cohete": "<:cohete_mineback:1455743005787951294>",
        "conexion": "<:Con_conex:1473479504365228084>",
        "shop": "<:asassa:1470495966967890002>"
    }
    embed = discord.Embed(
        description=(
            f"# {emoji_map['mineback']} - ¡POSTULACIONES ABIERTAS!\n\n"
            f"Postúlate siendo parte del Staff-Team. {emoji_map['sword']}\n\n"
            "🌐 **Opción Web:** Rellena el formulario en nuestra página.\n"
            "💬 **Opción Chat:** Responde las preguntas en Discord.\n\n"
            "# Requisitos:\n"
            f"{emoji_map['survival']}: Mínimo 14 Años.\n"
            f"{emoji_map['survival']}: Ser premium.\n"
            f"{emoji_map['survival']}: Historial limpio.\n"
            f"{emoji_map['survival']}: No ser staff en otro servidor.\n"
            f"{emoji_map['survival']}: Buena ortografía y madurez.\n\n"
            f"{emoji_map['cohete']} **¡Buena suerte!**\n\n"
            f"{emoji_map['mineback']} | mineback.xyz\n"
            f"{emoji_map['conexion']} | Puerto: 19132\n"
            f"{emoji_map['shop']} | https://tienda.mineback.xyz/"
        ),
        color=discord.Color.red()
    )
    await interaction.response.send_message("✅ Configurado!", ephemeral=True)
    await interaction.channel.send(embed=embed, view=BotonPostular())


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
    if not config.get("token"):
        print("❌ ERROR: Configura el token en config.json")
    else:
        hilo_web = threading.Thread(target=iniciar_servidor_web, daemon=True)
        hilo_web.start()
        try:
            bot.run(config["token"])
        except discord.LoginFailure:
            print("❌ Token inválido.")
        except Exception as e:

            print(f"❌ ERROR: {e}")
