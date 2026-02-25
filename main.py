import discord
from discord import app_commands
from discord.ui import Button, View
import requests
import os
from datetime import datetime
from bs4 import BeautifulSoup

# Config - aus Environment Variables (du setzt die später in Render)
DISCORD_TOKEN = os.environ['DISCORD_TOKEN']
TORN_API_KEY = os.environ['TORN_API_KEY']
CHANNEL_ID = os.environ.get('CHANNEL_ID', None)  # Optional - ID deines #torn-deals Kanals (rechtsklick > Copy ID)

# Liste von Tradern auf TornExchange - du kannst mehr hinzufügen!
TRADERS = ["Friends", "Noctir", "Sausage", "Flux", "Kivou", "Djj", "Spartan"]  # Bekannte Trader mit öffentlichen Listen

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# Cache für TE-Preise (höchster Buy-Preis pro Item)
te_prices = {}
last_te_update = 0

def update_te_prices():
    global te_prices, last_te_update
    now = datetime.now().timestamp()
    if now - last_te_update < 1800:  # Cache 30 Minuten
        return te_prices
    
    te_prices = {}
    headers = {'User-Agent': 'Mozilla/5.0'}  # Um wie ein Browser auszusehen
    for trader in TRADERS:
        url = f"https://tornexchange.com/prices/{trader}/"
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code != 200:
                continue
            soup = BeautifulSoup(response.text, 'html.parser')
            # Finde Tabelle - typisch <table> mit Items
            table = soup.find('table')
            if not table:
                continue
            rows = table.find_all('tr')[1:]  # Überspring Header
            for row in rows:
                cols = row.find_all('td')
                if len(cols) < 3:
                    continue
                try:
                    item_name = cols[0].text.strip()
                    item_id = int(cols[0].find('a')['href'].split('ID=')[1]) if 'href' in str(cols[0]) else None
                    buy_price_str = cols[1].text.strip().replace('$', '').replace(',', '')
                    buy_price = int(buy_price_str) if buy_price_str.isdigit() else 0
                    if item_id and buy_price > 0:
                        if item_id not in te_prices or buy_price > te_prices[item_id]:
                            te_prices[item_id] = buy_price
                except:
                    pass
        except:
            pass
    last_te_update = now
    return te_prices

async def scan_markets(interaction):
    await interaction.response.defer(ephemeral=False)  # Denkt nach...
    
    if not TORN_API_KEY:
        await interaction.followup.send("❌ Kein Torn API-Key gesetzt!")
        return
    
    update_te_prices()  # Hole frische TE-Preise
    if not te_prices:
        await interaction.followup.send("⚠️ Konnte keine Preise von TornExchange laden. Probiere später.")
        return
    
    deals = []
    
    # Hole alle Items von Torn API
    try:
        items_resp = requests.get(f"https://api.torn.com/torn?selections=items&key={TORN_API_KEY}")
        items = items_resp.json().get('items', {})
    except:
        await interaction.followup.send("❌ Fehler beim Laden der Item-Liste.")
        return
    
    # Filter auf Items mit Potenzial (market_value > 10k, um Calls zu sparen)
    item_ids = [int(k) for k, v in items.items() if v.get('market_value', 0) > 10000]
    
    for idx, item_id in enumerate(item_ids):
        if idx % 10 == 0:  # Status-Update alle 10 Items
            await interaction.followup.send(f"🔄 Scanne Item {idx}/{len(item_ids)}...", ephemeral=True)
        
        item_name = items[str(item_id)]['name']
        te_buy = te_prices.get(item_id, 0)
        if te_buy == 0:
            continue
        
        # Item Market
        try:
            market_resp = requests.get(f"https://api.torn.com/market/{item_id}?selections=itemmarket&key={TORN_API_KEY}")
            listings = market_resp.json().get('itemmarket', [])
            for lst in listings:
                cost = lst['cost']
                qty = lst['quantity']
                profit = (te_buy - cost) * qty
                if profit >= 50000 and cost < te_buy:
                    link = f"https://www.torn.com/imarket.php#/p=shops&step=shop&ID={item_id}"
                    deals.append(f"**Item Market: {item_name} x{qty}**\nKauf: ${cost:,} | Verkauf: ${te_buy:,} | Gewinn: ${profit:,}\n[Link]({link})")
        except:
            pass
        
        # Bazaar
        try:
            bazaar_resp = requests.get(f"https://api.torn.com/market/{item_id}?selections=bazaar&key={TORN_API_KEY}")
            listings = bazaar_resp.json().get('bazaar', [])
            for lst in listings:
                cost = lst['cost']
                qty = lst['quantity']
                profit = (te_buy - cost) * qty
                seller_id = lst.get('ID', '')  # Seller ID für Link
                if profit >= 50000 and cost < te_buy:
                    link = f"https://www.torn.com/bazaar.php?userID={seller_id}#p=shop&ID={item_id}" if seller_id else "Kein Link verfügbar"
                    deals.append(f"**Bazaar: {item_name} x{qty}**\nKauf: ${cost:,} | Verkauf: ${te_buy:,} | Gewinn: ${profit:,}\n[Link]({link})")
        except:
            pass
    
    # Ergebnis posten
    if not deals:
        embed = discord.Embed(title="Keine Deals gefunden", description="Aktuell nichts mit >= 50k Gewinn.", color=0xFF0000)
    else:
        embed = discord.Embed(title="Lukrative Deals (>= 50.000$ Gewinn)", color=0x00FF00, timestamp=datetime.utcnow())
        for deal in deals[:20]:  # Max 20, um nicht zu spammen
            embed.add_field(name="\u200b", value=deal, inline=False)
        if len(deals) > 20:
            embed.add_field(name="\u200b", value=f"... und {len(deals)-20} mehr (Scanne neu für alle).", inline=False)
    
    channel = client.get_channel(int(CHANNEL_ID)) if CHANNEL_ID else interaction.channel
    await channel.send(embed=embed)
    await interaction.followup.send("✅ Scan abgeschlossen!", ephemeral=True)

# Slash-Command: /deals - zeigt den Button
@tree.command(name="deals", description="Zeigt Button zum Abholen von Deals")
async def deals_command(interaction: discord.Interaction):
    class DealView(View):
        @discord.ui.button(label="Abholen", style=discord.ButtonStyle.green, emoji="💰")
        async def abholen_button(self, btn_interaction: discord.Interaction, button: Button):
            await scan_markets(btn_interaction)
    
    view = DealView()
    embed = discord.Embed(title="Torn Deals Bot", description="Drücke 'Abholen' für frische Deals!", color=0x3498DB)
    await interaction.response.send_message(embed=embed, view=view)

@client.event
async def on_ready():
    await tree.sync()
    print(f"Bot ist online: {client.user}")

client.run(DISCORD_TOKEN)
