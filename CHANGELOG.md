# Changelog

What's new in Wipebot, AI raid analysis from your guild's Warcraft Logs. Each change is tagged with where you'll notice it: `Agent` is the analysis itself, wherever you ask; `MCP` is Wipebot in Claude and ChatGPT; `Discord` is Wipebot in Discord.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Wipebot runs from its main branch: changes since the last version are under Unreleased. Versions before 0.1.0, the first public release, are one a day.

## [Unreleased]

## [0.7.0] - 2026-10-25

### Added

- `Agent` **Ask about the guild's raid plan, with no pull to check it against.** "What's our plan for Coiled Altar?", "Where do I stand in P2?" or "Who takes the orbs?" is answered from the plan as drawn on raidstrats.gg, scene by scene: each spot by the boss and the raid markers, what each number does from the plan's side panel, the notes, and what's drawn around them. A plan exported from Viserio with the night's names filled in answers for each raider by name.

### Fixed

- `Agent` **Asking about a boss you haven't pulled yet works.** The plan is shown as drawn and the newest saved note as it is, instead of an error saying the night has no such boss.
- `Agent` **A raid plan missing because the guild's raidstrats.gg login stopped working now says so.** Instead of "none saved", the answer says the plans can't be fetched and an officer has to connect again.
- `MCP` **No more empty replay box.** Questions about the raid plan answer in text, and the pull's replay with the plan's spots opens when you ask to see a pull against the plan. Over a whole night, the box shows the answer's map instead of nothing.

## [0.6.0] - 2026-10-24

### Changed

- `Discord` **Posts after a wipe come sooner.** A post starts from the pull, and the guides on what ended it, already looked up, so a typical one arrives about seven seconds sooner and the slowest about fifteen.

## [0.5.3] - 2026-10-24

### Fixed

- `Agent` **A late taunt is no longer read as no missed taunt.** The tank swaps now name a swap debuff that stacked past one while a tank held the boss, and when the other tank took it over, so "did I miss a taunt?" gets the late swap the log shows, still without calling it one tank's fault.

## [0.5.2] - 2026-10-23

### Fixed

- `MCP` **ChatGPT no longer counts the replay viewer as a write.** The two tools only the pull replay calls were listed under Write in ChatGPT, beside saving a raid note, as tools that may change something; they only read.

## [0.5.1] - 2026-10-23

### Fixed

- `MCP` **The plugin's connector in Codex.** Installed from its marketplace in Codex, also inside the ChatGPT desktop app, the plugin brought only the method for reading raid logs, without the connector to sign in to; it now brings both. ChatGPT's chats take the connector on its own.

## [0.5.0] - 2026-10-23

### Added

- `MCP` **The plugin in Codex and ChatGPT.** The same plugin, the connector with the method for reading raid logs, now also installs in Codex and the ChatGPT desktop app: `codex plugin marketplace add` with the plugin's repository, then install it from the Plugins directory and sign in with Warcraft Logs.

## [0.4.0] - 2026-10-23

### Added

- `Discord` **What Wipebot is looking at.** When an answer takes a while, a small line under your question shows which parts of the log Wipebot has read so far. It goes away when the answer is posted, and you're notified of the answer, not of the line.
- `Agent` **A realistic target against the top players.** Asked what the top players of a spec do on a boss, Wipebot now also says where the spec's kills spread (the 90th, 75th, 50th and 25th percentile), so you have a level to aim at rather than the single best parse. For a healer, it shows how many healers each top log had and how much of their healing the top healer did, next to your own raid's, since a healer's numbers depend on the healers around them.
- `MCP` **A public copy for any guild.** Wipebot now also runs at [wipebot.enfo.dev](https://wipebot.enfo.dev) for anyone with a Warcraft Logs account, free and best effort: in Claude, add the marketplace `Enf0/wipebot-plugin`; elsewhere, add `https://wipebot.enfo.dev/mcp` as a connector. It shares one Warcraft Logs allowance, so each user gets part of it per hour, and questions that need new data can wait for the next hour.

### Changed

- `MCP` Someone who is no longer signed in to Wipebot from any app is forgotten 90 days later: their Warcraft Logs name and characters are deleted.

### Fixed

- `Discord` A text chart in an answer no longer shows spell links as raw markdown inside it.

## [0.3.0] - 2026-10-23

### Added

- `MCP` `Discord` **A raid note pasted late.** Forgot to paste the note before the raid? Paste it afterwards with the pull it was used from, such as "from pull 7", and that pull and the later ones are checked against it, while the earlier ones keep that night's note saved before, if there was one.
- `Agent` **When the kick order changed.** Checking the night's kicks against a note that only fits the later pulls, Wipebot says from which pull it fits and who had the slots before, and counts the kicks from there, so no one is blamed for a slot that wasn't theirs yet. Tanks covering someone's kick are read as backup, not as another order.
- `Agent` **Holy Power lost at the cap.** Asked how a Holy Paladin or Preservation Evoker did, Wipebot now looks at their Holy Power or Essence as well as their mana: how long it sat full, how much was lost there and from which spells, on a pull and over the night, and how long the top players of the spec sit full on the same boss.
- `Agent` **Whose kick lost the pull.** Over a night, Wipebot counts per player whose cast went through first on each pull lost to kicks, since the casts after it come too late to matter, and lists every such pull.

### Changed

- `Agent` Advice on how to play your spec, such as what to cast or spend and when, now follows its class guides: Wipebot reads the spec's guide before telling you what to change.
- `Agent` The first "how did I do tonight?" about a night now answers about 20 seconds sooner: a player's night of 40 pulls is read in a few requests instead of eighty one after another.
- `MCP` `Discord` A saved raid note now counts only for the raid night it was pasted for, since the note changes each night with who comes: a night nothing was pasted for asks for the note instead of using another night's.
- `Agent` A kick on someone's cast that another player took, such as a tank backing up, now counts as covered instead of missed: the cast was handled. Who missed most is now whose casts went through, and the pulls lost within seconds of kicks going through are named, with which assignments they were.

### Fixed

- `Agent` Who had a kick ready when a cast went through now counts every spell a player kicked with that night, so a player with a second interrupt on hand is no longer read as having none.
- `MCP` `Discord` A raid note changed during a raid no longer carries over to the next raid night: the night's pulls before a note pasted late used the last night's note instead of that night's.
- `Agent` Asked who got caught by a mechanic that only puts a debuff on players, such as a wave that stacks one, Wipebot now ranks them by how often it caught them instead of by the little damage it dealt.

## [0.2.0] - 2026-10-22

### Added

- `Agent` **Week over week.** Ask whether the raid is doing better on a boss than last week, and Wipebot goes through each of the guild's raid nights on it: the pulls, the kill, the best wipe and what ended the pulls, night by night.
- `Agent` **Pulls by name.** Ask about the kill, the best wipe, the last wipe, or the first or last pull of a boss, and Wipebot finds it without looking up its number first.
- `Agent` **One stretch of a pull.** Who got hit by a mechanic can be counted for one stretch of each pull, such as the first set of orbs, without the rest of the pull mixing in.
- `Discord` **Answers in your DMs.** Ask Wipebot in a channel to send something to your DMs, such as who missed their kicks, and it answers there as if you'd asked there, where it may name players. It only ever writes to the one who asked.
- `MCP` **The raid plan in the replay.** In Claude, checking a pull against the raid plan opens the pull's replay with the plan's spots on it: a button for each time its mechanic happened, and at each, a red ring and a line from the spot of every player who stood off theirs.

### Changed

- `MCP` In Claude, the plugin's method now loads before Wipebot's first tool call on any raid question; smaller models such as Haiku had skipped it on most questions.
- `Agent` A question that assumes its answer, such as "do we need more DPS?", now gets that checked first: the answer opens with what the log shows lost the pull. Asked what to change, Wipebot says whether to keep the plan or change it, which damage spike needs a healing cooldown, and whether more damage or another healer would help at all.
- `Agent` Why a pull wiped now says whether it ended the way earlier pulls that night did, or got past the thing that ended most of them.
- `Agent` How the night went now counts the pulls by what ended each, so the one thing that ends most of them stands out; why a pull wiped says plainly when it wasn't the boss's enrage or a damage check; and who was hit, stood where or had a kick ready comes with a reminder that whose job it was isn't in the log.
- `Agent` Why a pull wiped now names the deaths that led into the moment that lost it, counts an ability that kills in pulses over a few seconds (a set of hits, a wave of adds) as one moment, and no longer starts a run of deaths at a player who was battle-ressed.
- `Agent` Asking about the adds over the whole night now also gives, for the most damaged kinds, when each wave of them died and each player's damage on them over the pulls.
- `Agent` Where everyone stood at a moment now also gives each player's distance to the player you ask about, such as the one carrying a mark the raid stacks on, so who was in range is measured rather than guessed from where they stood around the boss.

### Fixed

- `Agent` A late taunt is no longer called one tank's fault: the log shows who taunted when, not whose turn it was. Asked what stats to aim for, Wipebot gives the spec guide's priority and treats what the top players wear as a note beside it, not a new priority.
- `Agent` Comparing your talents with the top players says which part of the tree each is in (class, spec or hero), so it no longer suggests swapping a class talent for a spec talent.
- `Discord` In a DM, Wipebot knows you by your nickname in the guild's server, so a character named there is found without asking.
- `Agent` When Warcraft Logs is down, questions about a raid night Wipebot has already read are still answered from what it kept, instead of failing on the first step.
- `Agent` Comparing your talents with the top players no longer lists a spell with two versions on one choice node (such as Earthquake, Blizzard or Rain of Fire) as a talent you lack: Wipebot says you took the other version, and how the two differ.
- `Agent` Asked about the whole night while the next pull is under way, defensives, healing, damage and healing breakdowns, the adds, and checks against the raid note or plan no longer count that unfinished pull: they leave it out and say so, as the other answers about the night already did.
- `Agent` Asking whether a boss has an enrage now finds what the guides say about it, whether they call it an enrage or a berserk.

## [0.1.0] - 2026-10-21

### Added

- `Agent` `MCP` `Discord` **Wipebot is open source.** Any guild can run its own copy, under the GNU AGPL v3.0: the connector its members add to Claude, ChatGPT or another assistant, and the Discord bot. How to set it up: docs/setup.md.

## [0.0.11] - 2026-10-21

### Added

- `MCP` **Save the raid note or plan from Claude or ChatGPT.** An officer (by default the Guild Master and Officer ranks in the guild) pastes the note exported from Viserio, or the plan's export from raidstrats.gg, and asks Wipebot to save it for the guild. From then on, everyone's answers in Claude, ChatGPT and Discord check pulls against it without a paste. Anyone else's paste is used for that answer only.
- `Discord` **Officers save the raid note or plan by pasting it.** An admin sets the officers' role with `/wipebot setup officers:`; until one is set, the members who can manage the server are the officers. Officers also change or stop the posts after wipes someone else asked for in a channel. `/wipebot note` is gone: an officer pastes the note or plan in a message to Wipebot instead (Discord's file for a long paste works too), and `/wipebot settings` now lists what's saved.
- `Discord` **Names in the channels you choose.** An admin turns naming players on or off in any channel with `/wipebot names`: there, and in its threads, Wipebot says who made the costly mistakes, as in a DM. It replaces the officers' channel in `/wipebot setup`, and names stay on where that was.
- `Agent` **The adds over the whole night.** Ask how the adds went on a boss across the night and see how many of each came and died over the pulls, the damage they took, their casts and who kicked them, and each pull in a line. When the log records no deaths for an add, Wipebot says it can't show the order they were killed in.

### Changed

- `Agent` Questions about defensives and raid cooldowns are answered a few seconds sooner the first time they're asked about a night.
- `Agent` When the hour's Warcraft Logs allowance is nearly used up, the few questions that read many nights or pulls at once (the tanks' taunts over the weeks, attendance, the healing over a night, what a raider keeps dying to over the weeks) are left for later, with a note saying in how many minutes to ask again, so the other questions still get answers.
- `MCP` Signing in from Claude or ChatGPT now shows which app is asking and where you go back to once you're signed in, before the Warcraft Logs login: one click goes on. A sign-in finishes only in the browser it was started in.
- `Agent` Damage, healing, damage taken and casts over a pull or a night now stop where each wipe was decided, as Wipebot's other answers do, so what happened after the boss's enrage doesn't swamp the cause. Ask for the whole pull to include it.
- `Agent` Checking soaks against the raid note, a player hit by almost none of their group's soaks is named as likely not soaking with that group, such as a reserve or someone moved since the note was written, rather than only counted as missing them all.

### Fixed

- `MCP` A moment's hiccup at Warcraft Logs no longer signs you out of Wipebot in Claude or ChatGPT.
- `Agent` A boss named as one word, such as "twinfangs", is now found in questions about the night's logs, as it already was in the guides.
- `MCP` Where several guilds share one Wipebot, asking in Claude or ChatGPT about "our last raid" now means your own guild's. It used to be the first guild's for everyone.
- `Discord` A question asked while Wipebot was still writing a post after a wipe for you is answered even when that post takes too long or fails. It used to get "Something went wrong" as well. An answer under way when the next raid night's log starts now finishes, and when the logs take over a minute to load, Wipebot says so instead of waiting on.
- `Discord` When many raiders get their feedback after the same wipe, every answer now finishes. With more than six answers at once, one used to stop halfway with "Something went wrong". And after an answer failed, asking again starts afresh instead of failing the same way.
- `Agent` Rankings, attendance, a raider's logs and guides are no longer weeks old in the first answer after a quiet spell. That answer used to show what Wipebot had last read, however long ago, and only the next one was up to date.
- `Agent` `Discord` During a raid, a pull that ended while Wipebot was reading it now shows in full in later answers. Its deaths and numbers used to stop where the pull was when Wipebot first read it.
- `Agent` When Warcraft Logs stops answering for a moment during a raid, questions about the night are answered from what Wipebot read of the log seconds before. They used to fail.
- `Agent` After a patch, the spec cooldowns, talents and spell names Wipebot counts with are up to date within a day. They used to stay as they were until Wipebot restarted.
- `Agent` A shaman back from Reincarnation, or anyone else who comes back to life by themselves, now counts as alive again, as after a battle res: in the players alive after each death, when half the raid was dead, and whether they could have cast a planned cooldown or kick. They used to count as dead until they died again.
- `Agent` A Demon Hunter who dies stays dead in their time alive, time lost and cooldowns ready again until something brings them back. A cast Warcraft Logs adds by itself a second after the death used to count them alive for the rest of the pull.
- `Agent` Who took far more of a boss's damage than the rest now counts the time a player was alive again after a battle res. A player brought back early used to look as if they'd stood in everything.
- `Agent` Who had a kick ready when an add's cast went through now includes players brought back by a battle res. Once they had died, they used to be left out for the rest of the pull.
- `Agent` A cooldown that came back only after the wipe was decided is no longer called ready again and unused on a raider's pull: by then no use was missed.
- `Agent` When Warcraft Logs answers only part of a question about a night's pulls, Wipebot says so and to ask again. It used to fail with an unexpected error.
- `Discord` When Warcraft Logs isn't answering, Wipebot now says so and asks you to try again in a few minutes. It used to say nothing at all.
- `Discord` A post after a wipe no longer gives up while Wipebot is still answering your last question, and after a restart in the middle of the raid it posts about the latest wipe only, not about every wipe it missed. A question also no longer waits while Wipebot starts a conversation with someone else.
- `Agent` "Was Bloodlust up when I pressed my cooldown?" now finds it whichever kind the raid had, such as Time Warp or Primal Rage, and a buff someone else gave you, such as Power Infusion, now shows at your casts too. Wipebot used to see only the buffs you gave yourself.
- `Agent` Asked how a boss works on Normal, Wipebot now reads the guides' Heroic strategy. It used to read the Mythic one.
- `Agent` Where a player's damage or healing on a wipe stands among their spec's kills is measured against every ranked kill again. Early in a patch it could count only the best 100, so the same player read as below the 25th percentile one time and above the 75th the next.
- `Agent` One hit that kills ten players in a second is no longer split into deaths that count and "fallout" a few milliseconds later: when half the raid dies at once, every death of that moment counts, and the deaths in the seconds after it are fallout. Half the raid dead now means half, with a battle res counted, and a buff a council boss gains when its partner dies is no longer read as the moment the wipe was decided, nor as how the wipe ended or a reason to leave later deaths out of what killed the first players.
- `Agent` What killed players in each pull's post counts only the deaths until the wipe was decided, so an enrage's fallout no longer tops the list, and no killer drops off the end. A pull still going reads as in progress, not as a wipe or a new best, the latest finished pull is the one meant when no pull is named, and a raider's night, who took most of each mechanic over the night and a raider's deaths over the weeks leave it out until it ends.
- `Agent` Players alive after each death count a battle res, and many dying at once with no killing blow in the log reads as one moment that killed many, not as deaths one after another.
- `Agent` An ability the log records under several ids, such as Virulence, is counted once: a death after hits of both no longer counts twice, and the night's killer, who took most of a mechanic and the abilities behind a damage spike each show it as one. How many hits landed within a second of each other now says, right beside the count, that the log can't tell one hit from two.
- `Agent` A player's cooldown uses against possible uses count only until they died or the wipe was decided, so time dead and the fallout no longer add uses they could never have pressed. A night's count above what the base cooldown allows, such as 2/1, now says a talent or effect shortens it, as one pull's count did.
- `Agent` A player's casting gaps are no longer excused as a raid-wide pause because most of the raid was dead: a pause is when most of the living stopped casting.
- `Agent` A wipe compared with a kill now says how far behind the kill's pace the pull was and since when, whether the kill was over before the boss's enrage, and how many had died on each side by then, so a wipe lost to deaths no longer reads as a damage check. The side-by-side table is shorter.

## [0.0.10] - 2026-10-19

### Added

- `Agent` **Who didn't kick as assigned?** A kick order in the raid note, such as "Kick #1 left" called to one player as an add starts its cast, is now checked against the log: who kicked each cast, who didn't, and which casts went through, for a pull or the whole night. Whichever side's add starts casting first, each cast is put on the right player.
- `Discord` `MCP` **Raid plans fetched from raidstrats.gg.** An officer connects their raidstrats.gg login once with `/wipebot raidstrats`, and nobody pastes plans any more: whenever an answer needs a plan, Wipebot fetches the guild's plans, in Discord and in Claude and ChatGPT alike, and keeps a new version when one changed.

### Fixed

- `Agent` Asked about casts that went through, Wipebot now names the players who never kicked on the boss that night. It used to give only their number, and could name the wrong ones.
- `Agent` A boss pulled after a long break in the raid, such as the next boss after dinner, now shows up in Wipebot's answers. After a break of 20 minutes or more, Wipebot used to stop reading the night's log, and said the boss wasn't in it.
- `Discord` `MCP` Asked about the raid plan, Wipebot now says it fetched the plans from raidstrats.gg, as which account and when, and that a newer version was saved since the pulls were planned. Ask it to check against the newest version and it checks every pull against that one. It used to show only the version each pull was planned with, and said no raidstrats.gg login was connected when one was.
- `Discord` Answers in Discord are easier to skim: they open with one short bold line, a player's name is bold only the first time it comes up, and the fix for the next pull comes first instead of last.
- `Discord` `MCP` A raid plan pasted to Wipebot stays the one it uses until the plan changes on raidstrats.gg. The next fetch used to replace it with the plan as last saved there, which leaves out a roster given only in the planner's link.

## [0.0.9] - 2026-10-18

### Fixed

- `Agent` Casts per spell, yours and the top players', now count spells Warcraft Logs files under another one, such as Infernal Bolt under Shadow Bolt, Void Blast under Mind Blast or Fatebound Coin under Hand of Fate. They used to be missing, or shown as not counted.

## [0.0.8] - 2026-10-17

### Added

- `MCP` **The saved raid note and plan in Claude and ChatGPT.** "Did people do their jobs?" and "was everyone on their spot?" no longer need a paste: Wipebot checks your guild's pulls against the note and plan your officers saved in Discord.
- `Agent` **Where do I want my cooldowns?** Ask what the top players of a spec do on a boss and see when the top five pressed their major cooldowns, beside the boss's casts, and how many of the top 50 use each.
- `Agent` **Did the healers keep up?** Ask about a pull or the whole night and see when the raid took the most damage, which abilities it came from, how much healing went into it, each healer's healing and overhealing in those seconds, and which healing cooldowns were used or still ready. Over a night, the spikes that came back pull after pull are grouped, so you can see which one gets no cooldowns.

### Changed

- `MCP` **Sign in with Warcraft Logs.** Connecting Wipebot in Claude or ChatGPT now asks for your Warcraft Logs account instead of Battle.net, and knows your characters from the ones you claimed there. Claim your main on warcraftlogs.com first. If you're already connected, you'll be asked to sign in once more.
- `Agent` What killed a player now also says what each healer was casting in the last five seconds, and on whom.
- `Agent` "Would a defensive have saved them?" now has an answer for Exhilaration, Survival of the Fittest, Desperate Prayer, Ice Barrier, Blazing Barrier and Rallying Cry, where it used to say it couldn't tell.
- `Agent` Casts by player show how many a minute, so players on pulls of different lengths compare directly.
- `Agent` Wipebot refers to players by their character's name, never guessing "he" or "she".

### Fixed

- `Agent` A raid-wide stun or knockback no longer counts as your casting gap: stretches when most of the raid cast nothing are left out, and named.

- `Agent` A player brought back by a battle res counts as alive again: checks of the raid note's callouts and of who should have taken a mechanic no longer call them dead for the rest of the pull.
- `Agent` A pull's timeline says the boss's health it ended at, and the wipe's percentage beside it when the two differ (Warcraft Logs weighs phases into the wipe's), so 8.95% and 6.4% for the same pull no longer look like a contradiction.

## [0.0.7] - 2026-10-16

### Added

- `Agent` **Am I getting better?** How you did over a night now ends with your trend: deaths before each wipe was decided and your damage or healing per second alive, over the first, middle and last third of the pulls.

### Changed

- `Agent` How you did on a pull now shows when the boss cast its main abilities (Imbibe on Vashnik) beside your cooldowns, so a raid cooldown held for the damage isn't read as a late one.
- `Agent` **Who pulled?** A pull where one player hit the boss and nobody else followed is now called out, and pulls the raid reset are marked.

### Fixed

- `Agent` The pull timeline shows the boss's casts in the second they happened; they could show a second early.
- `Agent` Reading a page of a class guide that an earlier answer linked works for Wowhead and Icy Veins pages too; it could fail with an error.
- `Agent` Raid prep says how many players lack each consumable, not only who.
- `Agent` Adds that the log splits over two units (Vashnik's Malignant Totems) no longer show more deaths than came.
- `Agent` A buff both bosses gain at the end (the Coiled Altar's Soulbound) is listed once.
- `Agent` An add's cast whose start the log puts on another unit (a Drowned Echo's Soulcoiler's Curse) is counted as one cast that went through, as Warcraft Logs counts it, so the kick counts agree wherever you ask.
- `Agent` A channelled cooldown such as Divine Hymn counts once per use, not once per tick.
- `Agent` Whether a defensive would have saved you no longer counts on Cloak of Shadows lasting its full 5 seconds: its immunity is sure only for a moment.

## [0.0.6] - 2026-10-15

### Added

- `MCP` `Discord` **One Wipebot.** Wipebot in Claude and ChatGPT and in Discord are one now, with one name wherever you ask. Nothing to re-add: a connector you added before keeps working.
- `Discord` **Raid plans from raidstrats.gg.** An officer pastes the plan's export (the planner's Export button) to Wipebot, or attaches it to `/wipebot note`, and answers check where everyone stood against their spot: who was off it, by how far, and whose spot they took. A map shows the plan laid over where the raid stood, with a line from each missed spot to its player, or one player at every spread of the night. Paste it again after a change during the raid, and earlier pulls keep the version they were planned with.
- `Agent` **Where did we stand when it went off?** Ask where everyone stood when a mechanic happened, on any boss: each time a debuff ran out, the boss cast it or it hit, see who had it, who was hit by someone else's and how far apart they stood, on one pull or over the night, with a map of where you stood each time. In Claude and ChatGPT, paste the plan's export to check positions against it too.
- `Agent` **Your stats on the pull.** Asking how you did or how your build compares shows the crit, haste, mastery and versatility your gear gave you on that pull, straight from the log, so two players of the same spec can be compared stat by stat.
- `Agent` **Anything else in Warcraft Logs.** For a question no other answer covers, such as kills around your own item level rather than the fastest, the players in one fight of another guild's log, or events of one kind, Wipebot can now ask Warcraft Logs directly. Such answers take a little longer.
- `Agent` **Any guide or news page.** Wipebot can search Wowhead, Method, Icy Veins and Warcraft Wiki and read any of their pages in full, so a question no other answer covers, such as the rest of a news post, a guide's other pages or a wiki article, is looked up rather than turned down.
- `Agent` **Is it a 4-heal fight?** Ask what the top raids bring to a boss and see how many tanks, healers, melee and ranged the 50 fastest kills had, how fast each setup killed it, and links to the fastest kills.
- `Agent` **Who pulled?** Ask who keeps pulling the boss early and get who hit the boss first on each pull, with what, how far ahead of everyone else, and a count over the night. The log has no pull timer, so it shows who opens and who goes on their own, not how early against the countdown.

### Changed

- `Agent` **Who played well on the kill?** Damage and healing by player on a kill now show each player's spec and parse, so players of different specs are compared by how they did for their spec, not by raw numbers.
- `Discord` Posts after each wipe are a few short lines you can read before the next pull: how it ended, what decided it, and what to do next.
- `Agent` Comparing your build with the top players names the enchants they use on each slot where yours is missing or different, so "which enchant should I take?" is answered from the logs.
- `Agent` **Did people press what the note called for?** Callouts in the raid note, like "Big Personals" to everyone or a job for one player, are now checked against each pull: who pressed a defensive, raid cooldown or other spell around the call, and who had one ready and pressed nothing.
- `Agent` Asking how hard a spell hits shows its hits, average hit and crit rate per player, so "why does their Starsurge hit harder than mine?" gets numbers.
- `Agent` Asking about tank swaps shows when each tank used their taunt and when it was ready again, so "was the taunt on cooldown?" is answered from the log.
- `Agent` **The raid note over the whole night.** Ask "who missed their soak most?" or "who pressed personals when the note said?" and get each player counted over every pull on the boss, in seconds rather than a minute: soaks of their group they missed while alive, each planned cooldown on time, early, late or not cast, and how often they pressed something at each callout.
- `Agent` **When do your cooldowns go out?** How you did over the night now shows when you used each major cooldown on every pull, and when the first one usually went out, so "when does my first Arcane Surge go out?" gets the times and the outliers in one answer. Whether a buff was up when you cast a spell can be asked over every pull too.
- `Agent` **Your stats against the top players.** Comparing your build with the top players now shows their crit, haste, mastery and versatility from their own logs next to yours, as shares of the total, so "how do my stats compare with the top?" gets numbers rather than only the guide's stat priority.

- `Agent` **Was it the mechanics?** Comparing how you play with the top players now shows how much of the time your energy, rage or other resource sat full next to how much theirs did on the same boss, so "I was capped because of the mechanics" can be checked: they meet the same fight.
- `Agent` **Who took the bait?** Asking who took a mechanic meant for one player, such as a mark the furthest player gets, lists each time someone else got it over the night: the pull, when, how long they kept it and whether the player it was meant for was still alive. It answers in seconds rather than going through the night pull by pull.

### Fixed

- `Agent` A kill no longer reads as the boss left at 0.01%, which made a clean kill sound like a near miss.
- `Agent` On a fight with several bosses, a buff each of them gains, like the Entombed Sentinels' enrage, is counted once per pull rather than once per boss.
- `Agent` Checking a raid note against a pull that ended early, or against a kill shorter than the note, now still lists the cooldowns planned after that point, so a review of the note covers all of it instead of saying the rest can't be seen.
- `Agent` Adds the log records under two ids, such as some boss totems, are counted whole: how many died and how many were still up at the end no longer misses about half of them.
- `MCP` Claude and ChatGPT ask you to sign in to Wipebot again less often: a hiccup while they renew the connection no longer signs you out.
- `Agent` Dispel counts add up: each debuff applied was either dispelled or ran out, and a dispel that only took a stack off is counted apart. Before, the dispelled and ran-out counts could add up to more than were applied.
- `Agent` A Restoration Shaman's healing by spell shows the health Spirit Link Totem took from healthier players on a line of its own, out of the total and the shares; before, it was a negative row that lowered them.
- `Agent` Asking for your guild's other raid nights finds them without your server and region; before, the answer could stop to ask for them.
- `Discord` On a night with a single-boss raid as well as the tier's, Wipebot knows which raid each boss is in, so "the first boss" is the raid's first and the answer says which boss it took; before, a boss from the other raid pulled first could be taken for it.
- `Agent` A boss named any way ("Twinfangs", "Twin Fangs") finds its guides, all three of them; before, a name spelled differently from the guide sites could miss Method's guide or every guide.
- `Agent` Asking about a boss's hotfixes finds every one of Blizzard's changes since the raid opened; before, only Blizzard's newest hotfix post was read, so changes from before the latest patch were missed.
- `Agent` Asking about a boss's nerfs finds Wowhead's post about them and reads it; before, another boss with a similar name could hide it, and only the newest post about the boss was read.
- `Agent` When a guide site or Warcraft Logs can't answer, the part of the answer that needs it says it's unavailable and the rest still comes; before, it could read as no defensives, no cooldowns or not ranked, or fail the whole answer.
- `Agent` A defensive or raid cooldown cast as the talent that replaces it (Crusade for Avenging Wrath, the other Divine Protection) counts as used in a death's breakdown, the night's defensives and the raid note's callouts; before, it could read as never pressed.
- `Agent` Abilities are found by name the same way in every question: capitals and accents don't matter, and a name the log doesn't have gets the closest ones it does.
- `Discord` Posts after each wipe keep coming when Wipebot restarts during the raid; before, a restart ended them until someone asked again.
- `Agent` A wipe is called a new best only when it beats the guild's earlier nights on that boss too, not just tonight's pulls; the answer says where the guild got before, with a link to that pull.
- `Agent` A cooldown in the raid note that nobody has cast yet that night shows by its name, not as a spell number.
- `Agent` Spell descriptions include what applies to one spec only, so Death Grip shows as a taunt for Blood Death Knights.
- `Agent` Hit counts for an ability that has several versions in the log, like Raging Crosswinds on Sszorak, count all of them, not only the first.
- `Agent` A cooldown used before the pull, like Stormkeeper, counts as used when your cooldown use is checked; before, it looked like a missed use.

## [0.0.5] - 2026-10-14

### Added

- `Discord` **Posts after each wipe, wherever you ask.** Ask Wipebot in any channel or a DM to keep you posted during the raid, with whatever you want to hear after each wipe of the live log, like who died first and why or what to fix on the next pull. You can ask before the raid starts logging: the posts start with the first wipe. Anyone can ask it to stop.
- `Discord` **An officer channel.** Server admins can mark a channel only officers see in `/wipebot setup`. There Wipebot names players and says plainly who made the costly mistakes, in answers and in the posts after each wipe.
- `Agent` **What do I keep dying to?** Ask what you keep dying to, or what to work on this week, and get your early deaths on each of the guild's raid nights in recent weeks: how often you died before the wipe was decided, how often first, and the abilities that come back night after night.
- `Agent` **Deaths with no killing blow.** When the log names nothing that killed someone, the death's details now say where they were last seen and how far they had just moved, so being thrown or knocked off the platform shows.
- `Agent` **Did anyone have a kick ready?** For each add cast that got through, the answer can say who was alive with their interrupt off cooldown when it began, from the interrupts each player used on that boss.
- `Agent` **What top players play on a boss, before you've pulled it.** Ask what the top Balance Druids (or any spec) play on a boss and get what the best Warcraft Logs parses take: hero tree, the talents where they differ, how the guides' builds compare, trinkets, item level and kill time, links to the top logs and an import string to paste. It works for a boss your guild hasn't pulled yet, as soon as the first kills are ranked.

### Changed

- `Agent` Asking about tank swaps also shows which spells each tank has taken a boss with on the guild's recent raid nights, so whether a spell like Death Grip works as a taunt is answered from your own logs.
- `Discord` Player names in answers are in bold, so you can find who's mentioned at a glance.
- `Agent` Asking why a pull wiped starts from how it ended: one ability killing many at once, the boss's enrage, or deaths one after another, with when each came. A night's review names what killed the raid most, how often it was the first death, whom it killed and who took most of its hits.
- `Agent` Asking how you did or why your damage is low weighs your biggest losses against each other: time dead before the wipe was decided, time without casting and time at your resource's cap, so the answer leads with what cost you most.
- `MCP` In Claude and ChatGPT, each Wipebot tool says which questions it answers and how to read what it returns, and every tool has a readable name.

### Fixed

- `Agent` Asking how you did on a kill right after it no longer fails while Warcraft Logs hasn't ranked the kill yet; your parse shows once it has.
- `Discord` Answers about an earlier night, asked while another report is current, link their spells to Wowhead again.
- `MCP` ChatGPT no longer asks you to approve each Wipebot lookup: they only read your logs and the guides.
- `Discord` A talent named in an answer no longer gets a Wowhead link to a shorter spell inside its name ("Lycara's Inspiration" linked as "Inspiration").
- `MCP` The setup page's steps for ChatGPT match its current menus, and the server address has a copy button.

## [0.0.4] - 2026-10-13

### Added

- `Agent` **Boss loot, and what's in it for you.** Ask what a boss drops or whether there's anything good for your spec: each item's slot, stats and effect, the item level per difficulty, and which drops your spec's best-in-slot lists and trinket advice name.
- `Agent` `Discord` **Did everyone do their job?** Officers save the raid's assignment and cooldown note (the one exported from Viserio) with `/wipebot note`, or by pasting it to the bot. Then ask who missed their soak or whether the cooldowns went out on time. Each soak is checked against its group (who wasn't in it, alive or dead, and who was in the wrong one), and each planned cooldown against when it was really pressed. In Claude or ChatGPT, paste the note into the chat.
- `Agent` **Where do we stand among other guilds?** Ask how the guild compares to the guilds just above and below it on the realm, in the region or worldwide: each one's progress and, for the boss you're on, how many pulls they needed and their best pull.
- `Agent` **Adds' casts, and who kicked them.** The timeline and the adds tool show each add's casts with a cast bar, whether each was kicked (by whom) or went through, and when: "Broodling of Ithraz casts Visceral Burst x15: 8 kicked, 5 went through". Interrupts can be asked per player (who kicked how many) and per pull.
- `Agent` A stacking debuff reaching its highest stacks shows in the timeline ("Eternal Venom reaches 9 stacks on X"), and the boss casts that Lorrgs tracks for the fight are marked, to line pulls up by.
- `Agent` `MCP` Finding reports lists each boss with its pulls and kills, and can be narrowed to the reports a character is in.
- `Agent` **Would a defensive have saved me?** Looking at a death, Wipebot works out whether a personal defensive that was ready, or a healthstone, would have kept you alive through the hits that killed you. When several players died at once, it says which raid cooldowns were ready and whether the raid was stacked close enough for them.
- `Agent` **Everyone's healthstones and defensives.** Ask who used their healthstone, potions and personal defensives on a boss and you get the whole raid in one answer: uses per player, and at how many deaths a defensive was ready but unused.
- `Agent` **Buffs at your casts.** Ask whether a buff, or how many of its stacks, was up each time you cast a spell, like Starlord at your Starsurges.
- `MCP` **Wipebot in Claude and ChatGPT.** Add Wipebot as a connector and sign in with Battle.net to ask the same questions as in Discord, in your own chats. It's open to members of the guild.
- `MCP` **Wipebot knows which characters are yours.** Your Battle.net sign-in tells it which characters are yours, so "how did I do?" works without giving a name. Questions about reports start with the guild's own logs.
- `MCP` **Replay a pull.** In Claude, asking where everyone was during a pull opens a replay you can play, pause and scrub at different speeds. Everyone moves in their class colour, and you can see the boss and which way it faced. Each death and the moment the wipe was decided are marked on the timeline. Hover over a player to see their spec and their distance to the boss.
- `Discord` **Wipebot remembers you.** Once it knows which character you play, "how did I do?" works in every later chat without naming it. Ask it to keep a preference ("answer in English", "don't compare me with others by name") and it sticks to it from then on. Ask what it remembers about you, or tell it to forget, at any time.
- `Agent` `Discord` **Pull maps.** Ask "where was everyone when they died?" and you get the boss room at that moment. Every player is shown in their class colour, the dead where they fell, and which way the boss and each player faced. Everyone is named, and the players you asked about stand out.

### Changed

- `Discord` Items named in an answer link to their Wowhead page, the way abilities already do.
- `Agent` **Downtime and dropped casts.** "How did I do?" now shows the stretches where you cast nothing (when, and for how long) and the casts you began and dropped, each with its time, so you can check what was going on then. Next to the top parses, you see how much of their time was downtime and how often they dropped a cast, so movement the fight forces isn't held against you.
- `Agent` **Boss guides from three sources.** Method, Wowhead's cheat sheet and Icy Veins, and every boss of the raid is found (The Twin Fangs, Vashnik, the Coiled Altar, Kith'ix and others were missing Icy Veins' page). When one site has no Mythic guide, the answer says so and uses what it has.
- `Agent` Asking about a spec's rotation, cooldowns, defensives or talents gets the raiding sections of the guide (not the leveling or Mythic+ ones), longer, and with the date each page was last updated.
- `Agent` Hotfix questions also report Wowhead's news about the boss or spec when Blizzard's running hotfix post hasn't caught up with the weekly reset's tuning.
- `Agent` Interrupts and dispels add up: began, kicked (or dispelled), went through (or ran out), ended otherwise; a debuff hardly anyone dispels is marked as such, and dispels say on whom they ran out.
- `Agent` Adds that leave without dying (despawned, transformed, gone with the kill) are no longer called "alive at the end"; a boss-type add that comes several times (the Drowned Echoes) is listed with the adds.
- `Agent` One cast landing on several groups reads "Feasted on 6, then 6, then 7 players"; two abilities sharing a name read "Mutilate (2 abilities)", not "x2".
- `Agent` A pull number without a boss, on a night with several bosses, picks the boss that has it or asks which, instead of failing on the wrong boss.
- `Agent` **A player's resource is the one their spec spends.** Maelstrom, Essence, Holy Power and the rest are judged for capping; mana only for healers (an Elemental Shaman no longer gets "mana 100%").
- `Agent` A cooldown Lorrgs lists without a length (Elemental Ascendance) is still listed with its cast times, and one pressed more often than its base cooldown allows says so instead of "8/7".
- `Agent` Casts under two ids or a replacing talent (Mutilate per hand, Incarnation for Celestial Alignment) count as one spell, for the player and the top parses, so Celestial Alignment no longer reads "0/2" when Incarnation was cast.
- `Agent` "Far more" casts than top players is only flagged when the gap matters: one Bloodlust, potion, trinket or long cooldown on a short pull no longer is.
- `Agent` Talents are compared with the top parses of the player's own hero tree when enough play it, and the comparison says how many do.
- `Agent` A throughput cooldown used less than it could be says when it came back and in which phase, or that the player died first.
- `Agent` **Whole-night questions are much faster on a raid night.** On pulls nobody has asked about yet: how a player did over every pull about 70% faster, the raid's defensives and a table by pull about 95%, your deaths replayed about 60%, who stood in what over the night about 35%.
- `Agent` **When you cap your resource.** Asking about your pull says not only how much of the time your focus, energy or rage sat at its cap, but when: the longest stretches, so you can see whether it's the opener, movement or an intermission.
- `Agent` A name with a typo ("torvk" for Torvek) still finds the player when it's close to just one name.
- `Agent` Asking about a player on a boss they weren't on, or about an ability nobody used that night (a talent they didn't take), gets an answer that says where they were or names the closest abilities, instead of a plain "not found".
- `Agent` "Did I often die where a personal would have saved me?" is answered in one step over all your deaths on a boss, and is much faster.
- `MCP` In Claude and ChatGPT, questions about the latest raid work without naming a report: Wipebot takes the guild's latest raid night, as it does in Discord.
- `Agent` **Mechanic hits count ticks apart from hits.** A mechanic's damage ticks are no longer counted as separate hits. The count also flags a hit taken while the debuff from the last one was still up, with the time between the two.
- `Discord` Long conversations stay quick: after many questions in one place, Wipebot starts a fresh one with the recent messages instead of carrying every earlier answer along. Roughly 30-40% off the time and cost of answers late in a long conversation.
- `Discord` Wipebot offers feedback DMs after each wipe only when you ask for it in a DM and clearly want to keep up between pulls, not after every question about your own play.
- `Discord` When Wipebot restarts for an update, it finishes the answers it's working on first instead of dropping them.
- `Discord` Answers in Discord have tidier lists.
- `MCP` Wipebot has its icon in Claude and ChatGPT, and a new website: what you can ask (questions about the game too), the two ways to use it, setup and this changelog on pages of their own.

- `Discord` Replying to Wipebot just to tag a raidmate, to show them its answer, no longer gets a reply. A question in such a reply still gets one.

### Fixed

- `Agent` **"No ranked kills" when there were.** After a patch Warcraft Logs starts its rankings afresh, and for a while the new season's lists are empty: every comparison with top parses and kills, a player's season history and the guild's ranks then came back as "no ranked kills yet". The rankings of the season before are used until the new ones fill. An answer Warcraft Logs gave only in part is no longer kept for a day either.
- `Agent` A death recap no longer fails for a player who had an immunity (Divine Shield, Ice Block, Cloak of Shadows) ready.
- `Agent` Comparing a build against a guide no longer claims a player lacks the other hero tree's talents when the guide's import string carries both trees.
- `Agent` An add's death counted twice no longer gives "34 came, 59 died".
- `Discord` An ability named inside a longer one ("Soul Exhaustion", "Avenging Wrath") no longer gets the shorter ability's Wowhead link.
- `Discord` Asking Wipebot about another night ("what went wrong on Monday's raid?") no longer stumbles on finding the guild's reports before answering.
- `Agent` Asking for the guild's reports no longer passes off a raider's pug logged to the guild's page as a raid night: those are marked.
- `Agent` On long pulls with a lot going on, some answers looked at only the first part of the pull.
- `MCP` When Battle.net doesn't answer, you stay signed in, and signing in says so instead of failing with an error.
- `Agent` A player brought back with a battle res shows alive again on pull maps and in the replay, instead of staying dead where they first fell.
- `Agent` On a fight with several bosses, each boss gets its own marker on pull maps and in the replay.
- `MCP` In the replay, the marks showing which way a player or the boss faced no longer blink off as they turn.

## [0.0.3] - 2026-10-12

### Added

- `Agent` **Your pugs and alt runs count too.** Ask about a character's logs from any group, including pugs, other guilds and alts, and those runs are looked at the same way as the guild's.
- `Agent` **Parses by the spec you played.** If you change spec or role during the night, each pull is judged as the spec you played on it, and parses use that spec's numbers.
- `Agent` **Guild rankings and attendance.** You can ask "who's our best dps?" and get the raiders ranked as on the guild's rankings page. Ask who raided which nights and get attendance over the last few weeks.

### Changed

- `Discord` **No more posts after every wipe.** Wipebot no longer posts in the channel after each wipe. If you ask about your own pull during a live raid, it can send you your own feedback in a DM after each wipe.
- `Discord` **Answers keep up with the conversation.** Each answer sees what was said in the channel since the last question, so if one raider corrects something, the next answer knows it. A reply to the bot counts as a question even without a ping.
- `Discord` The report's date is stated, so an older log isn't called "tonight".
- `Agent` Pugs that raiders log to the guild's page are no longer treated as a guild raid night. They aren't followed live, aren't picked as the latest raid, and are left out of attendance.
- `Agent` Report overviews show each kill's speed and execution rankings and the guild's progress rank. Build checks now give your talents as an import string.
- `Discord` Wipebot answers in the language you ask in, keeps to the raid, and doesn't call people out by name in server channels.
- `Discord` Answers no longer end with a list of sources.

### Fixed

- `Agent` Percentiles against a spec's ranked kills were wrong for popular specs. How far a wipe got now uses the same measure as Warcraft Logs, which matters on bosses with phases.
- `Agent` A buff a boss gains when its partner dies is no longer read as an enrage. A raid cooldown from a damage dealer or tank counts when it actually reached other players.

## [0.0.2] - 2026-10-11

### Added

- `Agent` **A player's season.** Ask about a player's history and you get every ranked kill they've had this season, from any report, with the date, parse, item level and a link to the log.
- `Agent` **Smarter coaching.** Advice now looks first at waste: resources at their cap, charges left full, buffs and DoTs falling off, cooldowns held for nothing. It also judges healers' mana pacing and cooldown use, and tanks' active mitigation. A question about your night covers every boss you pulled.
- `Discord` **The latest raid night.** With no live log, questions go to the guild's latest real raid night instead of a small side log. If you're not in that report, Wipebot looks through the guild's other recent reports before asking which character is yours.

### Changed

- `Discord` After a wipe, Wipebot posted a single short answer about the pull (what lost it, what set it up and the one change to make) in place of a summary and a separate reply.
- `Agent` Answers are faster, and follow-up questions about the same report come back almost at once.

### Fixed

- `Agent` A player who left partway through the night was judged on bosses they never pulled.
- `Discord` Guilds on some realms couldn't be found during setup.
- `Agent` When asked how far back the logs go, only the latest few reports were seen.

## [0.0.1] - 2026-10-10

### Added

- `Discord` **First version.** Wipebot follows the guild's live Warcraft Logs report in Discord and answers raiders who @mention it or send it a DM. Server admins set it up with a single command.
- `Agent` **Why did we wipe?** Wipebot finds what ended a pull, like an enrage, too few players left for a mechanic, adds left alive or a cascade of deaths. Then it traces the pull back to the first mistake. Deaths after the wipe was decided count as fallout.
- `Agent` **Coaching for players.** Ask "how did I do?" or "why is my damage low?" and it checks your deaths, the mechanics you took, your throughput against the top players of your spec, your casts and cooldown timing, and your talents, gear and consumables.
- `Agent` **The whole night.** Ask what keeps wiping the raid, who keeps taking avoidable mechanics, and whether everyone came prepared. Each pull can be compared with a kill of the same boss.
- `Agent` **Tank swaps.** Ask about tanks and you get every taunt in order and each tank's debuff stacks.
- `Agent` Answers about boss strategy and class play draw on current guides, spell tooltips and recent hotfixes.
- `Discord` Wipebot introduces itself by its name in your server, keeps small talk short, and asks for your character only when a question needs it.
