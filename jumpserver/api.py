#coding: utf-8

from django.http import HttpResponseRedirect
import json
import os
from ConfigParser import ConfigParser
import getpass
from Crypto.Cipher import AES
from binascii import b2a_hex, a2b_hex
import ldap
from ldap import modlist
import hashlib
import datetime
import subprocess
from django.core.paginator import Paginator, EmptyPage, InvalidPage
from django.http import HttpResponse, Http404
from juser.models import User, UserGroup, DEPT
from jasset.models import Asset, BisGroup, IDC
from jlog.models import Log
from jasset.models import AssetAlias
from django.core.exceptions import ObjectDoesNotExist


BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
CONF = ConfigParser()
CONF.read(os.path.join(BASE_DIR, 'jumpserver.conf'))
LOG_DIR = os.path.join(BASE_DIR, 'logs')
SSH_KEY_DIR = os.path.join(BASE_DIR, 'keys')
SERVER_KEY_DIR = os.path.join(SSH_KEY_DIR, 'server')
KEY = CONF.get('web', 'key')
LOGIN_NAME = getpass.getuser()
LDAP_ENABLE = CONF.getint('ldap', 'ldap_enable')


# def user_perm_group_api(username):
#     user = User.objects.get(username=username)
#     if user:
#         perm_list = []
#         user_group_all = user.group.all()
#         for user_group in user_group_all:
#             perm_list.extend(user_group.perm_set.all())


class LDAPMgmt():
    def __init__(self,
                 host_url,
                 base_dn,
                 root_cn,
                 root_pw):
        self.ldap_host = host_url
        self.ldap_base_dn = base_dn
        self.conn = ldap.initialize(host_url)
        self.conn.set_option(ldap.OPT_REFERRALS, 0)
        self.conn.protocol_version = ldap.VERSION3
        self.conn.simple_bind_s(root_cn, root_pw)

    def list(self, filter, scope=ldap.SCOPE_SUBTREE, attr=None):
        result = {}
        try:
            ldap_result = self.conn.search_s(self.ldap_base_dn, scope, filter, attr)
            for entry in ldap_result:
                name, data = entry
                for k, v in data.items():
                    print '%s: %s' % (k, v)
                    result[k] = v
            return result
        except ldap.LDAPError, e:
            print e

    def add(self, dn, attrs):
        try:
            ldif = modlist.addModlist(attrs)
            self.conn.add_s(dn, ldif)
        except ldap.LDAPError, e:
            print e

    def modify(self, dn, attrs):
        try:
            attr_s = []
            for k, v in attrs.items():
                attr_s.append((2, k, v))
            self.conn.modify_s(dn, attr_s)
        except ldap.LDAPError, e:
            print e

    def delete(self, dn):
        try:
            self.conn.delete_s(dn)
        except ldap.LDAPError, e:
            print e

    def decrypt(self, text):
        cryptor = AES.new(self.key, self.mode, b'0000000000000000')
        try:
            plain_text = cryptor.decrypt(a2b_hex(text))
        except TypeError:
            raise ServerError('Decrypt password error, TYpe error.')
        return plain_text.rstrip('\0')


if LDAP_ENABLE:
    LDAP_HOST_URL = CONF.get('ldap', 'host_url')
    LDAP_BASE_DN = CONF.get('ldap', 'base_dn')
    LDAP_ROOT_DN = CONF.get('ldap', 'root_dn')
    LDAP_ROOT_PW = CONF.get('ldap', 'root_pw')
    ldap_conn = LDAPMgmt(LDAP_HOST_URL, LDAP_BASE_DN, LDAP_ROOT_DN, LDAP_ROOT_PW)
else:
    ldap_conn = None


def md5_crypt(string):
    return hashlib.new("md5", string).hexdigest()


def page_list_return(total, current=1):
    min_page = current - 2 if current - 4 > 0 else 1
    max_page = min_page + 4 if min_page + 4 < total else total

    return range(min_page, max_page+1)


def pages(posts, r):
    """分页公用函数"""
    contact_list = posts
    p = paginator = Paginator(contact_list, 10)
    try:
        current_page = int(r.GET.get('page', '1'))
    except ValueError:
        current_page = 1

    page_range = page_list_return(len(p.page_range), current_page)

    try:
        contacts = paginator.page(current_page)
    except (EmptyPage, InvalidPage):
        contacts = paginator.page(paginator.num_pages)

    if current_page >= 5:
        show_first = 1
    else:
        show_first = 0
    if current_page <= (len(p.page_range) - 3):
        show_end = 1
    else:
        show_end = 0

    return contact_list, p, contacts, page_range, current_page, show_first, show_end


class PyCrypt(object):
    """This class used to encrypt and decrypt password."""

    def __init__(self, key):
        self.key = key
        self.mode = AES.MODE_CBC

    def encrypt(self, text):
        cryptor = AES.new(self.key, self.mode, b'0000000000000000')
        length = 16
        try:
            count = len(text)
        except TypeError:
            raise ServerError('Encrypt password error, TYpe error.')
        add = (length - (count % length))
        text += ('\0' * add)
        ciphertext = cryptor.encrypt(text)
        return b2a_hex(ciphertext)

    def decrypt(self, text):
        cryptor = AES.new(self.key, self.mode, b'0000000000000000')
        try:
            plain_text = cryptor.decrypt(a2b_hex(text))
        except TypeError:
            raise ServerError('Decrypt password error, TYpe error.')
        return plain_text.rstrip('\0')


CRYPTOR = PyCrypt(KEY)


class ServerError(Exception):
    pass


def get_object(model, **kwargs):
    try:
        the_object = model.objects.get(**kwargs)
    except ObjectDoesNotExist:
        raise ServerError('Object get %s failed.' % str(kwargs.values()))
    return the_object


def require_login(func):
    """要求登录的装饰器"""
    def _deco(request, *args, **kwargs):
        if not request.session.get('user_id'):
            return HttpResponseRedirect('/login/')
        return func(request, *args, **kwargs)
    return _deco


def require_super_user(func):
    def _deco(request, *args, **kwargs):
        if request.session.get('role_id', 0) != 2:
            return HttpResponseRedirect('/')
        return func(request, *args, **kwargs)
    return _deco


def require_admin(func):
    def _deco(request, *args, **kwargs):
        if request.session.get('role_id', 0) < 1:
            return HttpResponseRedirect('/')
        return func(request, *args, **kwargs)
    return _deco


def is_super_user(request):
    if request.session.get('role_id') == 2:
        return True
    else:
        return False


def is_group_admin(request):
    if request.session.get('role_id') == 1:
        return True
    else:
        return False


def is_common_user(request):
    if request.session.get('role_id') == 0:
        return True
    else:
        return False


@require_login
def get_session_user_dept(request):
    user_id = request.session.get('user_id', 0)
    user = User.objects.filter(id=user_id)
    if user:
        user = user[0]
        dept = user.dept
        return user, dept


@require_login
def get_session_user_info(request):
    user_id = request.session.get('user_id', 0)
    user = User.objects.filter(id=user_id)
    if user:
        user = user.first()
        dept = user.dept
        return [user.id, user.name, user, dept.id, dept.name, dept]


def get_user_dept(request):
    user_id = request.session.get('user_id')
    if user_id:
        user_dept = User.objects.get(id=user_id).dept
        return user_dept.id


def api_user(request):
    hosts = Log.objects.filter(is_finished=0).count()
    users = Log.objects.filter(is_finished=0).values('user').distinct().count()
    ret = {'users': users, 'hosts': hosts}
    json_data = json.dumps(ret)
    return HttpResponse(json_data)


def view_splitter(request, su=None, adm=None):
    if is_super_user(request):
        return su(request)
    elif is_group_admin(request):
        return adm(request)
    raise Http404


def user_perm_group_api(username):
    if username:
        user = User.objects.get(username=username)
        perm_list = []
        user_group_all = user.group.all()
        for user_group in user_group_all:
            perm_list.extend(user_group.perm_set.all())

        asset_group_list = []
        for perm in perm_list:
            asset_group_list.append(perm.asset_group)
        return asset_group_list


def user_perm_group_hosts_api(gid):
    hostgroup = BisGroup.objects.filter(id=gid)
    if hostgroup:
        return hostgroup[0].asset_set.all()
    else:
        return []


def user_perm_asset_api(username):
    user = User.objects.filter(username=username)
    if user:
        user = user[0]
        asset_list = []
        asset_group_list = user_perm_group_api(user)
        for asset_group in asset_group_list:
            asset_list.extend(asset_group.asset_set.all())
        asset_list = list(set(asset_list))
        return asset_list
    else:
        return []


def asset_perm_api(asset):
    if asset:
        perm_list = []
        asset_group_all = asset.bis_group.all()
        for asset_group in asset_group_all:
            perm_list.extend(asset_group.perm_set.all())

        user_group_list = []
        for perm in perm_list:
            user_group_list.append(perm.user_group)

        user_permed_list = []
        for user_group in user_group_list:
            user_permed_list.extend(user_group.user_set.all())
        user_permed_list = list(set(user_permed_list))
        return user_permed_list


def get_user_host(username):
    """Get the hosts of under the user control."""
    hosts_attr = {}
    asset_all = user_perm_asset_api(username)
    user = User.objects.get(username=username)
    for asset in asset_all:
        alias = AssetAlias.objects.filter(user=user, host=asset)
        if alias and alias[0].alias != '':
            hosts_attr[asset.ip] = [asset.id, asset.ip, alias[0].alias]
        else:
            hosts_attr[asset.ip] = [asset.id, asset.ip, asset.comment]
    return hosts_attr


def get_connect_item(username, ip):
    asset = get_object(Asset, ip=ip)
    port = asset.port

    if not asset.is_active:
        raise ServerError('Host %s is not active.' % ip)

    user = get_object(User, username=username)

    if not user.is_active:
        raise ServerError('User %s is not active.' % username)

    login_type_dict = {
        'L': user.ldap_pwd,
    }

    if asset.login_type in login_type_dict:
        password = CRYPTOR.decrypt(login_type_dict[asset.login_type])
        return username, password, ip, port

    elif asset.login_type == 'M':
        username = asset.username
        password = CRYPTOR.decrypt(asset.password)
        return username, password, ip, port

    else:
        raise ServerError('Login type is not in ["L", "M"]')


def validate(request, user_group=None, user=None, asset_group=None, asset=None, edept=None):
    dept = get_session_user_dept(request)[1]
    if edept:
        if dept.name != edept[0]:
            return False

    if user_group:
        dept_user_groups = dept.usergroup_set.all()
        user_groups = []
        for user_group_id in user_group:
            user_groups.extend(UserGroup.objects.filter(id=user_group_id))
        if not set(user_groups).issubset(set(dept_user_groups)):
            return False

    if user:
        dept_users = dept.user_set.all()
        users = []
        for user_id in user:
            users.extend(User.objects.filter(id=user_id))

        if not set(users).issubset(set(dept_users)):
            return False

    if asset_group:
        dept_asset_groups = dept.bisgroup_set.all()
        asset_groups = []
        for group_id in asset_group:
            asset_groups.extend(BisGroup.objects.filter(id=int(group_id)))

        if not set(asset_groups).issubset(set(dept_asset_groups)):
            return False

    if asset:
        dept_assets = dept.asset_set.all()
        assets, eassets = [], []
        for asset_id in dept_assets:
            eassets.append(int(asset_id.id))
        for i in asset:
            assets.append(int(i)) 

        if not set(assets).issubset(eassets):
            return False

    return True


def verify(request, user_group=None, user=None, asset_group=None, asset=None, edept=None):
    dept = get_session_user_dept(request)[1]
    if edept:
        print dept.id, edept[0]
        if dept.id != int(edept[0]):
            return False

    if user_group:
        dept_user_groups = dept.usergroup_set.all()
        user_groups = []
        for user_group_id in user_group:
            user_groups.extend(UserGroup.objects.filter(id=user_group_id))
        if not set(user_groups).issubset(set(dept_user_groups)):
            return False

    if user:
        dept_users = dept.user_set.all()
        users = []
        for user_id in user:
            users.extend(User.objects.filter(id=user_id))

        if not set(users).issubset(set(dept_users)):
            return False

    if asset_group:
        dept_asset_groups = dept.bisgroup_set.all()
        asset_groups = []
        for group_id in asset_group:
            asset_groups.extend(BisGroup.objects.filter(id=int(group_id)))

        if not set(asset_groups).issubset(set(dept_asset_groups)):
            return False

    if asset:
        dept_assets = dept.asset_set.all()
        assets_id, dept_assets_id = [], []
        for a in dept_assets:
            dept_assets_id.append(int(a.id))
        for i in asset:
            assets_id.append(int(i))
        print assets_id, dept_assets_id
        if not set(assets_id).issubset(dept_assets_id):
            return False

    return True


def get_dept_asset(request):
    dept_id = get_user_dept(request)
    dept_asset = DEPT.objects.get(id=dept_id).asset_set.all()


def bash(cmd):
    """执行bash命令"""
    return subprocess.call(cmd, shell=True)


def is_dir(dir_name, username='root', mode=0755):
    if not os.path.isdir(dir_name):
        os.makedirs(dir_name)
        bash("chown %s:%s '%s'" % (username, username, dir_name))
    os.chmod(dir_name, mode)

