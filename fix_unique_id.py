#!/usr/bin/env python3.9
"""
Fix TikTokLive 6.4.5 unique_id recursion error on Pi
Run: python3.9 fix_unique_id.py
"""

import os

PROTO_PATH = "/home/pi/.local/lib/python3.9/site-packages/TikTokLive/proto/custom_proto.py"

with open(PROTO_PATH, 'r') as f:
    content = f.read()

print("Current unique_id property:")
# Find the property
idx = content.find('def unique_id')
print(content[idx:idx+200])
print()

# The problem: property calls itself causing infinite recursion
# Fix: access the underlying protobuf field directly

# Replace the recursive property with correct implementation
old_patterns = [
    # Pattern 1: calls self.unique_id (recursive)
    '''    @property
    def unique_id(self) -> str:
        """
        Retrieve the user's @unique_id
        :return: User's unique_id
        """
        return self.unique_id''',
    # Pattern 2: calls self.display_id
    '''    @property
    def unique_id(self) -> str:
        """
        Retrieve the user's @unique_id
        :return: User's unique_id
        """
        return self.display_id''',
    # Pattern 3: calls self.unique_id or self.nick_name
    '''    @property
    def unique_id(self) -> str:
        """
        Retrieve the user's @unique_id
        :return: User's unique_id
        """
        return self.unique_id or self.nick_name''',
]

new_property = '''    @property
    def unique_id(self) -> str:
        """
        Retrieve the user's @unique_id
        :return: User's unique_id
        """
        try:
            # Access protobuf field directly to avoid recursion
            val = self.__dict__.get('_unique_id') or self.__dict__.get('unique_id')
            if val:
                return str(val)
            # Try protobuf internal dict
            for key in ['uniqueId', 'unique_id', 'displayId', 'display_id']:
                val = self.__dict__.get(key)
                if val:
                    return str(val)
            return self.nick_name or ""
        except:
            return self.nick_name or ""'''

fixed = False
for old in old_patterns:
    if old in content:
        content = content.replace(old, new_property)
        print(f"Fixed pattern found and replaced!")
        fixed = True
        break

if not fixed:
    print("Pattern not found exactly - showing current unique_id area:")
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if 'unique_id' in line and 'def ' in line:
            print(f"Line {i}: {line}")
            for j in range(i, min(i+10, len(lines))):
                print(f"  {j}: {lines[j]}")
    print("\nTrying broader replacement...")
    # Find and replace the property block
    import re
    pattern = r'(@property\s+def unique_id[^@]+?)(?=@property|\Z)'
    match = re.search(pattern, content, re.DOTALL)
    if match:
        print("Found via regex:")
        print(match.group(0))
        content = content.replace(match.group(0), new_property + '\n\n    ')
        fixed = True
        print("Replaced!")

if fixed:
    with open(PROTO_PATH, 'w') as f:
        f.write(content)
    print(f"\n✅ Written to {PROTO_PATH}")
    print("Restart AEGIS: sudo systemctl restart aegis")
else:
    print("\n❌ Could not fix automatically - manual edit needed")
    print(f"Edit: {PROTO_PATH}")
