# utils.py
import re, aiohttp, random, string, logging
logger = logging.getLogger(__name__)

def generate_secret_key(length=8):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

async def get_public_ip():
    ipv4_apis = [
        'http://ipv4.ifconfig.me/ip',
        'http://api-ipv4.ip.sb/ip',
        'http://v4.ident.me',
        'http://ip.qaros.com',
        'http://ipv4.icanhazip.com',
        'http://4.icanhazip.com'
    ]
    async with aiohttp.ClientSession() as session:
        for api in ipv4_apis:
            try:
                async with session.get(api, timeout=5) as r:
                    if r.status == 200:
                        ip = (await r.text()).strip()
                        if re.match(r'^\d{1,3}(\.\d{1,3}){3}$', ip):
                            return ip
            except:
                continue
    return "[服务器公网ip]"
