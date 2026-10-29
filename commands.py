"""Parse owner commands without consulting model output, history, notes, or images."""
from dataclasses import dataclass
import re
import shlex


@dataclass(frozen=True)
class Command:
    action: str
    argument: str = ''


def _id(value, *, channel=False):
    pattern = r'<#([1-9][0-9]{0,19})>' if channel else r'<@!?([1-9][0-9]{0,19})>'
    if re.fullmatch(r'[1-9][0-9]{0,19}', value):
        result = value
    else:
        match = re.fullmatch(pattern, value)
        if not match:
            raise ValueError('Use an explicit Discord mention or numeric ID for the target.')
        result = match[1]
    if int(result) > 2**63 - 1:
        raise ValueError('Discord ID is out of range.')
    return result


def parse(text):
    """None for conversation, otherwise one fully validated command or ValueError."""
    if not re.match(r'^/lato(?:\s|$)', text.strip(), re.I):
        return None
    if '\n' in text or '\r' in text:
        raise ValueError('Send one /lato command per message.')
    parts = shlex.split(text.strip())
    if len(parts) < 2:
        raise ValueError('Usage: /lato ACTION [mention/ID] [value]. See README.md.')
    action, args = parts[1].lower(), parts[2:]
    if action in {'mute', 'unmute', 'deafen', 'undeafen', 'disconnect', 'untimeout'} and len(args) == 1:
        return Command(action.upper(), _id(args[0]))
    if action == 'timeout' and len(args) == 2:
        if not re.fullmatch(r'(?:[1-9]|[1-5][0-9]|60)m?', args[1]):
            raise ValueError('Timeout must be an integer from 1 to 60 minutes.')
        return Command('TIMEOUT', _id(args[0]) + ' -> ' + args[1].rstrip('m') + 'm')
    if action == 'move' and len(args) == 2:
        return Command('MOVE', _id(args[0]) + ' -> ' + _id(args[1], channel=True))
    if action == 'nick' and len(args) >= 2:
        nick = ' '.join(args[1:])
        if len(nick) > 32:
            raise ValueError('Nickname must be at most 32 characters.')
        return Command('NICK', _id(args[0]) + ' -> ' + ('' if nick == '-' else nick))
    if action in {'pin', 'unpin', 'delete', 'join', 'leave'} and not args:
        return Command({'join': 'JOIN_VC', 'leave': 'LEAVE_VC'}.get(action, action.upper()))
    if action == 'thinking' and args in (['on'], ['off'], ['toggle']):
        return Command('TOGGLE_THINKING' if args[0] == 'toggle' else 'THINKING', args[0])
    if action in {'status', 'vc-status'} and args:
        value = ' '.join(args)
        if len(value) > (128 if action == 'status' else 500):
            raise ValueError('Status text is too long.')
        return Command('VC_STATUS' if action == 'vc-status' else 'STATUS', value)
    if action in {'remember', 'forget'} and args:
        value = ' '.join(args)
        if len(value) > 140 or '->' in value or '[[' in value:
            raise ValueError('Use a plain note of at most 140 characters about yourself.')
        return Command(action.upper(), value)
    raise ValueError('Invalid /lato command or arguments. See README.md for command syntax.')
