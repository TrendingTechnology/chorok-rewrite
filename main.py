import sys

import dico  # noqa

import utils
from models import ChorokBot

config = utils.config.load("config.json", sys.argv[1])
if not config:
    raise SystemError("invalid config")

bot = ChorokBot(
    config=config,
    token=config.token["discord"],
    prefix="",
    intents=dico.Intents.full(),
    default_allowed_mentions=dico.AllowedMentions(),
)

if __name__ == "__main__":
    bot.run()
