GROUP = {"id": "lights", "label": "Lights", "help": "Smart bulbs on the LAN (Meross/Refoss). Setup: MODULES.md -> Lights."}
SETTINGS = [
    {"key": "mod_lights", "group": "modules", "label": "Lights", "kind": "bool", "default": False,
     "help": "She can switch and colour the smart lights when you ask. Needs the device list + key (see MODULES.md)."},
    {"key": "lights_who", "label": "Who can control the lights", "kind": "choice", "default": "trusted",
     "choices": ["owner", "trusted", "friends"],
     "help": "owner = only you; trusted = you + friends marked trusted (Friends card); friends = everyone on the friends list."},
]
