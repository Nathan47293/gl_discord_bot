async def register_commands(bot):
    """
    Dynamically load all command extension modules (cogs) at startup.
    """
    for module in ["alliances", "members", "colonies", "war"]:
        await bot.load_extension(f"bot.commands.{module}")