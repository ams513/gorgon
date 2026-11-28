"""Teacher lesson packs for Stark World Engine Classroom mode.

Each pack is a starter project plus a goal, the steps and a "check your work" list. The starter worlds are plain
world.json data (schema in studio_app.WORLD_SCHEMA_DOC); nothing here needs the network or runs student code.
Time estimates are suggestions for planning only."""
import math

MOOD = 'Alpine Sunset'


def _o(t, x, z, s=1, **kw):
    d = {'t': t, 'x': round(x, 2), 'z': round(z, 2), 's': s}
    d.update(kw)
    return d


def _world(name, story, objects, **kw):
    w = {'name': name, 'mode': '3d', 'mood': MOOD, 'seed': 7, 'version': 2, 'story': story, 'size': 40,
         'weather': 'clear', 'spawn': {'x': 0, 'z': -30}, 'objects': objects}
    w.update(kw)
    return w


def _talk(speaker, hello, offer, accept, qid, wait, done):
    """One quest-giver dialog: offer -> accept (starts the quest) / decline; then wait and thank-you branches."""
    return {'start': 'hi',
            'branches': [{'if': {'done': qid}, 'node': 'done'}, {'if': {'active': qid}, 'node': 'wait'}],
            'nodes': {'hi': {'speaker': speaker, 'text': hello + ' ' + offer,
                             'choices': [{'text': accept, 'start': qid, 'goto': 'yes'},
                                         {'text': 'Not right now.', 'end': True}]},
                      'yes': {'speaker': speaker, 'text': 'Thank you. Come back when it is done.'},
                      'wait': {'speaker': speaker, 'text': wait},
                      'done': {'speaker': speaker, 'text': done}}}


# ---------------- 1. loops and patterns ----------------
def _loops():
    objs = [_o('lamp', -10 + 4 * i, -8) for i in range(6)]                               # repeat 6: place, move 4 m
    objs += [_o('crystal', 12 * math.cos(i * math.pi / 4), 12 * math.sin(i * math.pi / 4)) for i in range(8)]
    objs += [_o('tree', -18, -18 + 4 * i, 1.1) for i in range(4)]
    return _world('Lesson - Lantern Row',
                  'A row of 6 lamps, a ring of 8 crystals and a short line of trees. Each one is a pattern: '
                  'something repeated with a fixed step. Find the step, then repeat it yourself.', objs)


# ---------------- 2. cause and effect with quests ----------------
def _cause():
    objs = [_o('npc', 6, -4, 1, id='mira', name='Mira', dialog='mira_talk'),
            _o('crystal', -8, 6), _o('crystal', 10, 12), _o('crystal', -14, -6), _o('crystal', 4, 18),
            _o('tree', 14, -2), _o('tree', -4, -14), _o('lamp', 8, -2)]
    quests = [{'id': 'q_lantern', 'title': 'Light the way', 'auto': False, 'giver': 'mira', 'final': True,
               'desc': 'Mira needs 3 crystals to light the path.',
               'steps': [{'type': 'collect', 'item': 'crystal', 'count': 3, 'label': 'Collect 3 crystals'}],
               'reward': {'xp': 50, 'gold': 10}}]
    dialogs = {'mira_talk': _talk('Mira', 'Hello, traveller.', 'The path is dark. Can you bring me 3 crystals?',
                                  'I will help.', 'q_lantern', 'Still looking? There are crystals all around.',
                                  'The path is bright again. Thank you!')}
    return _world('Lesson - The Dark Path', 'Talking to Mira is the cause. A quest starting is the effect. '
                  'Finish the quest and the game ends: another effect.', objs, quests=quests, dialogs=dialogs)


# ---------------- 3. coordinates and maps ----------------
def _coords():
    objs = [_o('lamp', x, 0) for x in (-20, -10, 10, 20)] + [_o('lamp', 0, z) for z in (-20, -10, 10, 20)]
    objs += [_o('chest', -16, 12, 1, item='gem', n=1), _o('chest', 14, 14, 1, item='gem', n=1),
             _o('chest', 18, -14, 1, item='gem', n=1), _o('rock', -6, -6), _o('rock', 6, 8)]
    quests = [{'id': 'q_map', 'title': 'Follow the map', 'auto': True, 'final': True,
               'desc': 'The map lists three places as (x, z). Walk to each one.',
               'steps': [{'type': 'reach', 'x': -16, 'z': 12, 'r': 3, 'label': 'Go to (-16, 12)'},
                         {'type': 'reach', 'x': 14, 'z': 14, 'r': 3, 'label': 'Go to (14, 14)'},
                         {'type': 'reach', 'x': 18, 'z': -14, 'r': 3, 'label': 'Go to (18, -14)'}],
               'reward': {'xp': 40}}]
    return _world('Lesson - Treasure Map',
                  'Lamps mark the x axis (left and right) and the z axis (forward and back) every 10 metres. '
                  'Three chests sit at coordinates from the quest. The centre is (0, 0).', objs, quests=quests)


# ---------------- 4. story structure ----------------
def _story():
    def one_line(sp, text):
        return {'start': 'hi', 'nodes': {'hi': {'speaker': sp, 'text': text}}}
    l1 = _world('Lesson - Three Acts',
                'ACT 1 - SETUP. Meet the hero, the place and the problem. The village well has gone dry.',
                [_o('npc', 4, -6, 1, id='scout', name='Scout', dialog='scout_talk'), _o('crystal', -6, 8),
                 _o('house', -10, -4), _o('tree', 12, 6), _o('portal', 0, 22, 1, to='next', lock='q_act1')],
                quests=[{'id': 'q_act1', 'title': 'The well is dry', 'auto': True, 'next': 'q_act2',
                         'desc': 'Collect a crystal to learn where the water went.',
                         'steps': [{'type': 'collect', 'item': 'crystal', 'count': 1, 'label': 'Find a crystal'}]}],
                dialogs={'scout_talk': one_line('Scout', 'The well dried up overnight. Something upstream is wrong.')})
    l2 = {'name': 'Act 2: The Conflict', 'mode': '3d', 'mood': MOOD, 'seed': 8, 'size': 40, 'weather': 'clear',
          'story': 'ACT 2 - CONFLICT. The hero faces an obstacle: a creature blocks the stream.',
          'spawn': {'x': 0, 'z': -30},
          'objects': [_o('enemy', 0, 8, 1, kind='grunt', id='dam_slime', name='Dam Slime', hp=20),
                      _o('tree', -10, 0), _o('rock', 10, 4), _o('portal', 0, 24, 1, to='next', lock='q_act2')],
          'quests': [{'id': 'q_act2', 'title': 'Clear the stream', 'next': 'q_act3',
                      'desc': 'Defeat the slime that is blocking the water.',
                      'steps': [{'type': 'defeat', 'kind': 'grunt', 'count': 1, 'label': 'Defeat the slime'}]}]}
    l3 = {'name': 'Act 3: The Resolution', 'mode': '3d', 'mood': MOOD, 'seed': 9, 'size': 40, 'weather': 'clear',
          'story': 'ACT 3 - RESOLUTION. The problem is solved and the world is different because of it.',
          'spawn': {'x': 0, 'z': -30},
          'objects': [_o('npc', 0, 4, 1, id='elder3', name='Elder', dialog='elder_talk'), _o('lamp', 6, 4)],
          'quests': [{'id': 'q_act3', 'title': 'Bring the news', 'final': True,
                      'desc': 'Tell the Elder the water runs again.',
                      'steps': [{'type': 'talk', 'npc': 'elder3', 'label': 'Talk to the Elder'}],
                      'reward': {'xp': 60}}],
          'dialogs': {'elder_talk': {'start': 'hi', 'nodes': {'hi': {
              'speaker': 'Elder', 'text': 'The water runs again. Thank you.', 'do': {'advance': 'elder3'}}}}}}
    l1['levels'] = [l2, l3]
    return l1


# ---------------- 5. simple economy with items ----------------
def _economy():
    objs = [_o('npc', 6, -4, 1, id='tomas', name='Tomas', dialog='tomas_talk')]
    objs += [_o('item', x, z, 1, item='herb') for x, z in ((-8, 6), (10, 10), (-12, -8), (14, -10))]
    objs += [_o('chest', 18, 6, 1, gold=15), _o('chest', -18, 12, 1, item='fine_lamp', n=1), _o('lamp', 8, -2)]
    items = {'herb': {'name': 'Moon Herb', 'kind': 'quest', 'desc': 'Tomas buys these for his remedies.'},
             'bread': {'name': 'Bread', 'kind': 'consumable', 'heal': 25, 'gold': 5, 'desc': 'Heals 25. Worth 5 gold.'},
             'fine_lamp': {'name': 'Fine Lamp', 'kind': 'treasure', 'gold': 40, 'desc': 'Worth 40 gold.'}}
    quests = [{'id': 'q_herbs', 'title': 'Herbs for Tomas', 'giver': 'tomas', 'final': True,
               'desc': 'Tomas pays 30 gold and a loaf of bread for 4 herbs.',
               'steps': [{'type': 'collect', 'item': 'herb', 'count': 4, 'label': 'Collect 4 Moon Herbs'}],
               'reward': {'gold': 30, 'items': ['bread']}}]
    dialogs = {'tomas_talk': _talk('Tomas', 'Good day.', 'I will pay 30 gold and a loaf of bread for 4 Moon Herbs.',
                                   'Deal.', 'q_herbs', 'Four herbs, remember. Search the meadow.',
                                   'A fair trade. Thank you.')}
    return _world('Lesson - Market Day', 'Everything has a value. Count what you can earn, then decide if the '
                  'prices feel fair.', objs, items=items, quests=quests, dialogs=dialogs)


# ---------------- 6. debugging a broken level ----------------
def _broken(fixed=False):
    """fixed=False has four bugs: no key for the door, too few crystals, the Warden points at a dialogue that does
    not exist, and the dialogue that does exist never starts the quest."""
    objs = [_o('npc', 6, -4, 1, id='warden', name='Warden', dialog='warden_talk' if fixed else 'warden_chat'),
            _o('door', 0, 14, 1, key='brass_key'), _o('crystal', -8, 6), _o('lamp', 8, -2),
            _o('tree', -12, -4), _o('rock', 12, 8)]
    if fixed:
        objs += [_o('crystal', 10, 12), _o('crystal', -14, 12), _o('item', -16, -10, 1, item='brass_key')]
    quests = [{'id': 'q_gate', 'title': 'Open the gate', 'giver': 'warden', 'final': True,
               'desc': 'Gather 3 crystals, then open the brass gate.',
               'steps': [{'type': 'collect', 'item': 'crystal', 'count': 3, 'label': 'Collect 3 crystals'}],
               'reward': {'xp': 30}}]
    items = {'brass_key': {'name': 'Brass Key', 'kind': 'key', 'desc': 'Opens the brass gate.'}}
    if fixed:
        dialog = _talk('Warden', 'Halt.', 'Bring me 3 crystals and the gate is yours.', 'On it.', 'q_gate',
                       'Three crystals. I am counting.', 'The gate is yours.')
    else:
        dialog = {'start': 'hi', 'nodes': {'hi': {'speaker': 'Warden',
                                                  'text': 'Bring me 3 crystals and the gate is yours.'}}}
    return _world('Lesson - The Broken Level',
                  'This level was meant to be finishable. It is not. Play it, find out why, and fix it.',
                  objs, items=items, quests=quests, dialogs={'warden_talk': dialog})


PACKS = [
    {'id': 'loops', 'title': 'Loops and patterns: Lantern Row', 'concept': 'Loops and patterns', 'minutes': 40,
     'goal': 'Describe a pattern as "repeat N times, move by a fixed step", then build new patterns that way.',
     'steps': ['Open the lesson and press Play. Walk along the row of lamps and around the ring of crystals.',
               'Switch to Build and click one lamp, then the next. Write down the x and z of the first two lamps.',
               'Work out the step: how far apart are the lamps? How many are there? Say it as "repeat 6 times, move 4".',
               'Look at the ring of 8 crystals. Is it the same kind of pattern? What changes each time?',
               'Add a 7th lamp to the row by using the step you found.',
               'Build a new pattern of your own: a square of trees, a staircase of rocks, or a second ring.'],
     'check': ['The lamps in my row are all the same distance apart.',
               'I can write my pattern as "repeat ... times, move ...".',
               'My new pattern uses at least 5 objects.',
               'I can explain which number I changed to make the pattern longer.'],
     'reflect': ['What was the repeating step in your pattern?',
                 'Where did you make a mistake in spacing, and how did you fix it?',
                 'Where would you see a loop outside a game?'],
     'build': _loops},
    {'id': 'cause', 'title': 'Cause and effect: The Dark Path', 'concept': 'Cause and effect with quests', 'minutes': 45,
     'goal': 'Trace a chain of causes and effects through a quest, then add one of your own.',
     'steps': ['Open the lesson and play it. Talk to Mira with E and accept her request.',
               'Open the Quests tab. Read the quest. What starts it? What finishes it?',
               'Draw the chain on paper: talk to Mira, the quest starts, crystals are counted, the quest completes.',
               "Open the Dialogue tab and change Mira's offer so she asks for 4 crystals. Change the quest step to match.",
               'Add a second person with a second quest that starts only after the first one is done.',
               'Play your version from the start and check that every effect happens.'],
     'check': ['I can say what the cause is and what the effect is for each quest.',
               'The number of crystals Mira asks for matches the quest step.',
               'There are at least as many crystals in the level as the quest needs.',
               'My second quest can be started and finished.'],
     'reflect': ['Write one cause and its effect from your game.', 'What happened when the numbers did not match?',
                 'What would break if you removed the dialogue?'],
     'build': _cause},
    {'id': 'coords', 'title': 'Coordinates and maps: Treasure Map', 'concept': 'Coordinates and maps', 'minutes': 40,
     'goal': 'Read and write (x, z) positions and use them to place things where you mean to.',
     'steps': ['Open the lesson. The lamps mark the x axis and the z axis every 10 metres. The centre is (0, 0).',
               'In Build mode click a lamp and read its x and z. Do this for 3 lamps. What do you notice?',
               'Play the level and follow the quest: it lists three places as (x, z).',
               'Draw the map on squared paper: mark (0, 0), the lamps, and the three places.',
               'Place a new chest at a point you choose. Write its coordinates down before you place it.',
               'Swap coordinates with a partner: they place a rock where your list says, then you check it.'],
     'check': ['I can tell which direction x goes and which direction z goes.',
               'My drawn map matches the three quest places.',
               'My new chest is at the coordinates I wrote down.',
               'My partner placed a rock within a few metres of my coordinates.'],
     'reflect': ['How do you describe a place using two numbers?', 'Which number did you mix up first, x or z?',
                 'Where do you see coordinates outside a game?'],
     'build': _coords},
    {'id': 'story', 'title': 'Story structure: Three Acts', 'concept': 'Story structure', 'minutes': 50,
     'goal': 'Name the setup, conflict and resolution of a story and build each as a level of a game.',
     'steps': ["Play all three levels. Each level's story text starts with ACT 1, ACT 2 or ACT 3.",
               'For each act write one sentence: who wants what, and what is in the way?',
               "Open the World tab and read each level's story text.",
               "Change the problem in Act 1 (not a dry well). Rewrite the story text and the scout's line to match.",
               'Change the obstacle in Act 2 so it fits your new problem, for example a different enemy.',
               'Rewrite the ending in Act 3 so it answers the problem you chose.'],
     'check': ['Act 1 shows the hero, the place and the problem.',
               'Act 2 puts something in the way.',
               'Act 3 resolves the problem from Act 1.',
               'The three levels can be played from the start to the end.'],
     'reflect': ['What is the problem in your story, and how is it solved?', 'Which act was hardest to write and why?',
                 'What would you change if you had one more level?'],
     'build': _story},
    {'id': 'economy', 'title': 'A simple economy: Market Day', 'concept': 'Items, value and trade', 'minutes': 45,
     'goal': 'Give items a value, count how much a player can earn, and tune prices so the trade feels fair.',
     'steps': ['Open the Items tab. Read the gold value of each item.',
               "Play the level. Collect herbs, open both chests and finish Tomas's quest.",
               'Make a table: where does gold come from? How much is there in total?',
               "Change Tomas's offer: 60 gold for 4 herbs. Play again. Does it feel too easy?",
               'Pick a price you think is fair and write down why.',
               'Add one new item with a value and a place to find it.'],
     'check': ['I can add up all the gold a player can earn in my level.',
               'The quest asks for the same number of herbs that exist in the level.',
               'My new item has a name, a kind and a gold value.',
               'I can say why my price is fair.'],
     'reflect': ['How much gold can a player earn in your level?', 'What price did you pick and why?',
                 'What would happen if everything were free?'],
     'build': _economy},
    {'id': 'debug', 'title': 'Debugging: The Broken Level', 'concept': 'Debugging', 'minutes': 45,
     'goal': 'Find why a level cannot be finished, form a guess, test one change at a time, and fix it.',
     'steps': ['Play the level and try to finish it. Write down exactly where you get stuck.',
               'Press "Check my level" in the Classroom tab. It lists things worth a look. It does not fix anything.',
               'Pick one problem. Say what you expect to be wrong before you change anything.',
               'Change one thing, then play again. Did that problem go away?',
               'Repeat until the level can be finished from start to end.',
               'Write down every problem you found and what fixed it.'],
     'check': ['I can finish the level from start to end.',
               '"Check my level" shows no problems.',
               'I changed one thing at a time and tested after each change.',
               'I can describe each bug in one sentence.'],
     'reflect': ['List each bug and how you fixed it.', 'How did you know where to look first?',
                 'What would you check first next time?'],
     'answer_key': ['The door needs a Brass Key and no key exists in the level (place the item).',
                    'The quest needs 3 crystals and only 1 exists (place 2 more).',
                    'The Warden points at the dialogue "warden_chat", which does not exist (the existing one is "warden_talk").',
                    'The "warden_talk" dialogue never starts the quest "q_gate", so it can never begin.'],
     'build': _broken},
]


def list_packs():
    return [{k: v for k, v in p.items() if k not in ('build', 'answer_key')} for p in PACKS]   # answer keys stay out of the app


def get_pack(pid):
    for p in PACKS:
        if p['id'] == pid:
            return p
    raise KeyError(f'no such lesson pack: {pid}')


def pack_world(pid):
    """A fresh copy of the starter world for a pack (built each call, so edits never leak between students)."""
    return get_pack(pid)['build']()


def solved_debug_world():
    """The Broken Level with every bug fixed (used to prove the lesson is fixable; not served to students)."""
    return _broken(fixed=True)
