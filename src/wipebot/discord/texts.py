"""The bot's own words to raiders, in English and Norwegian: what it says when a question gets no answer, the line
under a question while its answer is written, the steps under an answer, and its buttons and menus. The model writes
the answers and follows the question's language by itself; these are the words no model writes, all in this one
file, so whoever reads Norwegian checks them in one place.

Norwegian as the raiders write it: short and plain, with the game's words in English (pull, which is "pullet", wipe,
kill, deaths, adds, defensives, healing, spec, roster, gear). What admins read (`/wipebot setup`, `/usage`, `/help`)
stays English.
"""
import re
from dataclasses import dataclass

# Words far more common in one language than the other, to tell which a question or an answer is in: Norwegian or
# English
NORWEGIAN = re.compile(r"(?i)(?<!\w)(?:og|ikke|det|er|på|du|som|med|til|av|har|var|den|jeg|dere|hvor|hva|også|hvorfor"
                       r"|hvem|hvordan|når|vi|kan|min|mitt|meg|siste|ble|noen|fra)(?!\w)")
ENGLISH = re.compile(r"(?i)(?<!\w)(?:and|not|the|is|on|you|with|to|of|has|was|it|are|what|also|your|why|who|how|when"
                     r"|we|can|my|did|do|last|any|from)(?!\w)")


def language(text: str, unclear: str = "en") -> str:
    """'no' for a text in Norwegian and 'en' for one in English, by which language's common words it has more of;
    `unclear` when it has as many of each (a short question, a bare link)."""
    no, en = len(NORWEGIAN.findall(text)), len(ENGLISH.findall(text))
    return "no" if no > en else "en" if en > no else unclear


def locale_language(locale: object) -> str:
    """'no' for a Discord client in Norwegian, else 'en'."""
    return "no" if str(locale).startswith(("no", "nb", "nn")) else "en"


def named(name: str | None) -> str:
    """'no' when the language an admin set for the server (/wipebot setup language:, as they wrote it) is Norwegian,
    else 'en'."""
    name = (name or "").strip().lower()
    return "no" if name.startswith(("norw", "nors", "bokm", "nyn")) or name in ("no", "nb", "nn") else "en"


@dataclass(frozen=True)
class Text:
    """One thing the bot says, in English and in Norwegian, with the same {fields} in both."""
    en: str
    no: str

    def to(self, lang: str, **fields: object) -> str:
        """It in `lang` ('no', else English), with its {fields} filled in."""
        return (self.no if lang == "no" else self.en).format(**fields)


# ---------- a question that gets no answer ----------

OUT_OF_CREDIT = Text("I'm out of credit for answering, so I can't answer for now. `/usage` shows the budget.",
                     "Jeg er tom for kreditt, så jeg kan ikke svare akkurat nå. `/usage` viser budsjettet.")
RATE_LIMIT = Text("Too many questions at once; try again in a minute.",
                  "For mange spørsmål på en gang. Prøv igjen om et minutt.")
NO_MODEL = Text("I can't reach Claude right now; try again in a few minutes.",
                "Jeg får ikke kontakt med Claude akkurat nå. Prøv igjen om noen minutter.")
TOO_MUCH = Text("That needed more digging than I do for one answer, sorry. Try asking something narrower.",
                "Det krevde mer graving enn jeg gjør for ett svar, beklager. Prøv å spørre om noe mer konkret.")
TOO_SLOW = Text("That took too long, sorry. Try asking something narrower.",
                "Det tok for lang tid, beklager. Prøv å spørre om noe mer konkret.")
SLOW_START = Text("Loading the report took too long; try again in a minute.",
                  "Det tok for lang tid å laste loggen. Prøv igjen om et minutt.")
WENT_WRONG = Text("Something went wrong while answering that; try again in a moment.",
                  "Noe gikk galt mens jeg svarte. Prøv igjen om litt.")
WCL_DOWN = Text("Warcraft Logs isn't answering, so I can't look at the logs; try again in a few minutes.",
                "Warcraft Logs svarer ikke, så jeg får ikke sett på loggene. Prøv igjen om noen minutter.")
# A report Warcraft Logs won't show the asker (wcl.Unreadable), and how they'd let the bot read a private one
UNREADABLE = Text("Warcraft Logs won't show me report {code}: it's private, was deleted, or the code is wrong, so "
                  "asking again won't help.",
                  "Warcraft Logs vil ikke vise meg logg {code}: den er privat, slettet, eller koden er feil, så det "
                  "hjelper ikke å spørre igjen.")
PRIVATE_LOGIN = Text(" If it's a private report you can open on Warcraft Logs, `/login private:True` lets me read it "
                     "for your questions; then ask again.",
                     " Er det en privat logg du kan åpne på Warcraft Logs, lar `/login private:True` meg lese den for "
                     "spørsmålene dine. Spør igjen etterpå.")
# A server on a bot that answers only on servers' own keys, before an admin added one: the first thing a new server
# reads
NEEDS_KEY = Text("This server hasn't added an AI key for my answers yet: an admin adds one with `/wipebot setup` "
                 "(OpenRouter or Anthropic).",
                 "Denne serveren har ikke lagt inn en AI-nøkkel for svarene mine ennå: en admin legger inn en med "
                 "`/wipebot setup` (OpenRouter eller Anthropic).")
STILL_ANSWERING = Text("I'm still answering {n} of your questions: ask again when one is done.",
                       "Jeg svarer fortsatt på {n} av spørsmålene dine. Spør igjen når ett er ferdig.")
ASKED_A_LOT = Text("That's {n} answers for you this hour, which is all one person gets: ask again in a while.",
                   "Det er {n} svar til deg denne timen, og mer får ikke én person. Spør igjen om en stund.")
TOO_LONG = Text("That's a long one: ask me in at most {chars} characters.",
                "Den var lang: spør meg med maks {chars} tegn.")
RESTARTING = Text("I'm restarting; ask again in a minute.", "Jeg starter på nytt. Spør igjen om et minutt.")
NEEDS_ROLE = Text("Chatting with wipebot needs the <@&{role}> role.",
                  "Du trenger rollen <@&{role}> for å chatte med wipebot.")
MEMBERS_ONLY = Text("I can only chat with members of a server where wipebot is set up (with the role it asks for, if "
                    "any).",
                    "Jeg kan bare chatte med medlemmer av en server der wipebot er satt opp (med rollen den krever, om "
                    "den krever en).")
NOT_HERE = Text("I can't answer here.", "Jeg kan ikke svare her.")

# ---------- around an answer ----------

CUT = Text("-# The answer was cut here: it ran past four messages.",
           "-# Svaret ble kuttet her: det ble lengre enn fire meldinger.")
ASKED = Text("-# {name} asked: {question}", "-# {name} spurte: {question}")  # a follow-up clicked, posted as theirs
# The last post of a request, once its report ended: a raider's own night in their DMs, the raid's in a channel
NIGHT_MINE = Text("**That's the raid.** {character}'s night:", "**Det var raidet.** Kvelden til {character}:")
NIGHT_RAID = Text("**That's the raid.** The night on the last boss; the button under it shows yours, only to you.",
                  "**Det var raidet.** Kvelden på siste boss. Knappen under viser din, bare til deg.")

# ---------- the line under a question while its answer is written ----------

THINKING = Text("Thinking", "Tenker")
STARTED = Text("started", "startet")  # before a Discord timestamp, which counts up in the reader's own language
MORE = Text("+{n} more", "+{n} til")
# What a tool under way does, for the people waiting: a tool missing here shows its title
DOING = {
    "find_reports": Text("Finding reports", "Finner logger"),
    "roster": Text("Checking the roster", "Sjekker rosteren"),
    "pull_summary": Text("Summing up the pull", "Oppsummerer pullet"),
    "pull_timeline": Text("Reading the timeline", "Leser tidslinja"),
    "death_recap": Text("Reading death recaps", "Leser death recaps"),
    "mechanic_hits": Text("Counting mechanic hits", "Teller treff av mechanics"),
    "pull_map": Text("Drawing the map", "Tegner kartet"),
    "tank_swaps": Text("Checking tank swaps", "Sjekker tank swaps"),
    "compare_pulls": Text("Comparing pulls", "Sammenligner pulls"),
    "enemies": Text("Checking the adds", "Sjekker adds"),
    "raid_note": Text("Reading the raid note", "Leser raid-noten"),
    "raid_plan": Text("Reading the raid plan", "Leser raid-planen"),
    "raid_prep": Text("Checking flasks, food and gear", "Sjekker flasks, mat og gear"),
    "progress": Text("Going through the night", "Går gjennom kvelden"),
    "player_pull": Text("Reading a player's pull", "Leser en spillers pull"),
    "compare_with_top": Text("Comparing with top players", "Sammenligner med toppene"),
    "player_night": Text("Reading a player's night", "Leser en spillers kveld"),
    "player_history": Text("Reading a player's past weeks", "Leser en spillers tidligere uker"),
    "defensives": Text("Checking defensives", "Sjekker defensives"),
    "healing": Text("Reading the healing", "Leser healingen"),
    "buff_at_casts": Text("Checking buffs at casts", "Sjekker buffs ved casts"),
    "breakdown": Text("Breaking it down", "Ser på tallene"),
    "wcl_query": Text("Querying Warcraft Logs", "Spør Warcraft Logs"),
    "spell_info": Text("Looking up a spell", "Slår opp en spell"),
    "boss_guide": Text("Reading the boss guide", "Leser boss-guiden"),
    "web_lookup": Text("Looking it up on the web", "Slår det opp på nett"),
    "spec_guide": Text("Reading the spec guide", "Leser spec-guiden"),
    "hotfixes": Text("Checking hotfixes", "Sjekker hotfixes"),
    "follow_pulls": Text("Setting up posts after wipes", "Setter opp poster etter wipes"),
    "remember": Text("Taking a note", "Noterer"),
    "answer_in_dm": Text("Taking it to DMs", "Tar det i DM"),
}
# The pull a call is about, in the brackets after what it does
PULL = Text("pull {n}", "pull {n}")
EVERY_PULL = Text("every pull", "alle pulls")
PULLS = {"kill": Text("the kill", "killet"), "best wipe": Text("the best wipe", "beste wipe"),
         "last wipe": Text("the last wipe", "siste wipe"), "first": Text("the first pull", "første pull"),
         "last": Text("the last pull", "siste pull")}
# A step done, and the steps under an answer, in Norwegian: in English a step is its tool's title (server.title)
STEPS_NO = {
    "find_reports": "Logger", "roster": "Roster", "pull_summary": "Pullet", "pull_timeline": "Tidslinja",
    "death_recap": "Death recap", "mechanic_hits": "Treff av mechanics", "pull_map": "Kartet",
    "tank_swaps": "Tank swaps", "compare_pulls": "Pulls sammenlignet", "enemies": "Adds", "raid_note": "Raid-noten",
    "raid_plan": "Raid-planen", "raid_prep": "Flasks, mat og gear", "progress": "Kvelden",
    "player_pull": "En spillers pull", "compare_with_top": "Mot toppene", "player_night": "En spillers kveld",
    "player_history": "Tidligere uker", "defensives": "Defensives", "healing": "Healing",
    "buff_at_casts": "Buffs ved casts", "breakdown": "Tallene", "wcl_query": "Warcraft Logs", "spell_info": "Spell",
    "boss_guide": "Boss-guiden", "web_lookup": "Nettsøk", "spec_guide": "Spec-guiden", "hotfixes": "Hotfixes",
    "follow_pulls": "Poster etter wipes", "remember": "Notat", "answer_in_dm": "Svar i DM",
}

# ---------- buttons and menus ----------

# What each button under a picture opens, named so a raider sees where it goes ({boss}, {pull} and {player}: what it
# acts on)
BUTTONS = {
    "death": Text("First death: {player}", "Første død: {player}"),
    "top": Text("{player} against top players", "{player} mot toppene"),
    "night": Text("{player}'s night", "Kvelden til {player}"),
    "ppull": Text("{player}'s pull", "Pullet til {player}"),
    "mynight": Text("My night on {boss}", "Min kveld på {boss}"),
    "mypull": Text("How did I do?", "Hvordan gikk det for meg?"),
    "hits": Text("{player} hits pull by pull", "{player}-treff per pull"),
}
OPEN = Text("Open in Warcraft Logs", "Åpne i Warcraft Logs")
# What a choice in a menu under a picture opens
MENUS = {
    "player": Text("Open a player's card on this pull…", "Åpne en spillers kort på dette pullet…"),
    "ppull": Text("Open {player}'s card on a pull…", "Åpne kortet til {player} på et pull…"),
    "pull": Text("Open a pull on {boss}…", "Åpne et pull på {boss}…"),
}
# What a click is told, to the one who clicked alone
NOT_YOURS = Text("That's <@{asker}>'s follow-up. Ask me yourself: mention me with your question.",
                 "Det er oppfølgingen til <@{asker}>. Spør meg selv: nevn meg med spørsmålet ditt.")
CANT_ASK = Text("I can't ask that one: ask me in a message.", "Den får jeg ikke spurt: spør meg i en melding.")
WHICH_CHARACTER = Text("I don't know which character is yours yet. Tell me in a message (\"my character is ...\") and "
                       "I'll remember it.",
                       "Jeg vet ikke hvilken character som er din ennå. Si det i en melding («min character er ...»), "
                       "så husker jeg det.")
NOT_IN_PULL = Text("{player}, the character I have for you, wasn't in {pull}. If you play another one, tell me in a "
                   "message (\"my character is ...\").",
                   "{player}, characteren jeg har for deg, var ikke med i {pull}. Spiller du en annen, si det i en "
                   "melding («min character er ...»).")
CANT_DRAW = Text("I couldn't draw that one.", "Den klarte jeg ikke å tegne.")
CANT_READ = Text("I couldn't read that report on Warcraft Logs.", "Jeg fikk ikke lest den loggen på Warcraft Logs.")
