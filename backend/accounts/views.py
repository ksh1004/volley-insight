"""회원 가입, 로그인, 관심 팀·선수 API(WEB-11, WEB-12, 설계서 1.11).

토큰 인증(DRF authtoken): Vue 앱이 토큰을 보관하고 요청마다 `Authorization: Token ...` 머리글로 보낸다.
저장하는 것은 이메일, 닉네임, 해시된 비밀번호뿐이다(NFR-03, NFR-31). 회원은 언제든 탈퇴할 수 있다.
"""
from django.contrib.auth import authenticate, password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from league.models import Player, Team

from .feed import favorite_feed
from .models import FavoritePlayer, FavoriteTeam, User

MAX_FAVORITES = 10  # 종류(팀/선수)마다 최대 개수


def user_data(user):
    """응답에 넣을 회원 정보(닉네임이 비어 있으면 이메일 앞부분)."""
    return {"email": user.email, "nickname": user.nickname or user.email.split("@")[0],
            "is_staff": user.is_staff, "date_joined": user.date_joined}


def favorites_data(user):
    """회원의 관심 팀·선수 목록(등록한 순서대로)."""
    teams = user.favorite_teams.select_related("team").order_by("created_at")
    players = user.favorite_players.select_related("player").order_by("created_at")
    return {"teams": [{"code": f.team.code, "name": f.team.name, "gender": f.team.gender} for f in teams],
            "players": [{"code": f.player.code, "name": f.player.name, "gender": f.player.gender} for f in players]}


def session(user, code=status.HTTP_200_OK):
    """로그인 응답: 토큰(없으면 새로 만듦), 회원 정보, 관심 목록. 가입과 로그인이 함께 쓴다."""
    token, _ = Token.objects.get_or_create(user=user)
    return Response({"token": token.key, "user": user_data(user), "favorites": favorites_data(user)}, status=code)


class SignupSerializer(serializers.Serializer):
    """가입 입력 검사: 이메일 형식·중복, 닉네임 길이, 비밀번호 규칙(Django 기본 검사기들: 길이, 흔한 비밀번호 등)."""

    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(max_length=128, trim_whitespace=False)
    nickname = serializers.CharField(max_length=20, min_length=2)

    def validate_email(self, value):
        """이메일은 소문자로 맞춰 저장하고, 대소문자만 다른 중복도 막는다."""
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("이미 가입된 이메일입니다.")
        return value

    def validate(self, attrs):
        """비밀번호 검사. '이메일과 비슷한 비밀번호' 검사를 위해 저장하지 않는 임시 사용자 객체를 넘긴다."""
        probe = User(email=attrs["email"], username=attrs["email"], nickname=attrs["nickname"])
        try:
            password_validation.validate_password(attrs["password"], user=probe)
        except DjangoValidationError as e:
            raise serializers.ValidationError({"password": list(e.messages)})
        return attrs


class LoginSerializer(serializers.Serializer):
    """로그인 입력 검사(형식만. 맞는지는 authenticate가 확인)."""

    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(max_length=128, trim_whitespace=False)


class AuthView(APIView):
    """가입·로그인 공통: 세션 인증을 끄고, IP마다 시도 횟수를 제한한다(settings의 auth 속도)."""

    # 여기서는 세션/CSRF를 쓰지 않는다: 오래된 관리자 세션 쿠키가 토큰 로그인을 막으면 안 되므로
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


class SignupView(AuthView):
    """POST /api/auth/signup {email, password, nickname} -> {token, user, favorites}"""

    def post(self, request):
        s = SignupSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        try:
            with transaction.atomic():
                user = User.objects.create_user(username=d["email"], email=d["email"], password=d["password"],
                                                nickname=d["nickname"].strip())
        except IntegrityError:  # 같은 이메일로 동시에 두 번 가입한 경우(DB 유일성 제약에 걸림)
            return Response({"email": ["이미 가입된 이메일입니다."]}, status=status.HTTP_400_BAD_REQUEST)
        return session(user, status.HTTP_201_CREATED)


class LoginView(AuthView):
    """POST /api/auth/login {email, password} -> {token, user, favorites}"""

    def post(self, request):
        s = LoginSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        email = s.validated_data["email"].strip().lower()
        # createsuperuser로 만든 계정은 username이 이메일과 다를 수 있어, 이메일로 username을 먼저 찾는다
        found = User.objects.filter(email__iexact=email).only("username").first()
        user = authenticate(request, username=found.username if found else email, password=s.validated_data["password"])
        if not user:
            # 두 경우(없는 이메일, 틀린 비밀번호)에 같은 문구: 어떤 이메일이 가입돼 있는지 드러내지 않기 위해
            return Response({"detail": "이메일 또는 비밀번호가 맞지 않습니다."}, status=status.HTTP_400_BAD_REQUEST)
        return session(user)


class LogoutView(APIView):
    """POST /api/auth/logout: 토큰을 지운다(모든 기기에서 로그아웃)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    """GET /api/me: 로그인한 회원과 관심 목록. DELETE /api/me {password}: 비밀번호 확인 후 탈퇴."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"user": user_data(request.user), "favorites": favorites_data(request.user)})

    def delete(self, request):
        if not request.user.check_password(request.data.get("password") or ""):
            return Response({"password": ["비밀번호가 맞지 않습니다."]}, status=status.HTTP_400_BAD_REQUEST)
        request.user.delete()  # 관심 목록과 토큰도 함께 지워진다(CASCADE)
        return Response(status=status.HTTP_204_NO_CONTENT)


# 관심 종류 → (관심 모델, 대상 모델, 필드 이름, 회원 쪽 관계 이름)
KINDS = {"team": (FavoriteTeam, Team, "team", "favorite_teams"),
         "player": (FavoritePlayer, Player, "player", "favorite_players")}


def _kind(kind):
    """kind 파라미터 검사(team/player가 아니면 400)."""
    if kind not in KINDS:
        raise serializers.ValidationError({"kind": "team or player"})
    return KINDS[kind]


class FavoriteListView(APIView):
    """GET /api/me/favorites: 관심 목록. POST {kind: team|player, code}: 관심 추가(이미 있으면 그대로, 최대 10개)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(favorites_data(request.user))

    def post(self, request):
        model, target, field, related = _kind(request.data.get("kind"))
        obj = get_object_or_404(target, code=str(request.data.get("code") or ""))
        mine = getattr(request.user, related)
        if not mine.filter(**{field: obj}).exists():
            if mine.count() >= MAX_FAVORITES:
                return Response({"detail": f"관심 {'팀은' if field == 'team' else '선수는'} {MAX_FAVORITES}개까지 추가할 수 있습니다."},
                                status=status.HTTP_400_BAD_REQUEST)
            model.objects.get_or_create(user=request.user, **{field: obj})
        return Response(favorites_data(request.user), status=status.HTTP_201_CREATED)


class FavoriteDetailView(APIView):
    """DELETE /api/me/favorites/{kind}/{code}: 관심 해제. 바뀐 목록을 돌려준다."""

    permission_classes = [IsAuthenticated]

    def delete(self, request, kind, code):
        model, _, field, _ = _kind(kind)
        model.objects.filter(user=request.user, **{f"{field}__code": code}).delete()
        return Response(favorites_data(request.user))


class FeedView(APIView):
    """GET /api/me/feed: 홈 화면 '내 관심' 블록(관심 팀의 성적·지난/다음 경기, 관심 선수의 마지막 경기·시즌 기록)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(favorite_feed(request.user))
