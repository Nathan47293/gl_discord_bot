# bot/commands/__init__.py
async def register_commands(bot):
    for module in ["alliances", "members", "colonies", "war"]:
        await bot.load_extension(f"bot.commands.{module}")
