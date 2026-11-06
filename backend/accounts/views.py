"""Sign-up, login and favourites (WEB-11, WEB-12; design.md 1.11).

Token authentication (DRF authtoken): the Vue app keeps the token and sends `Authorization: Token ...`.
Only email, nickname and the hashed password are stored (NFR-03, NFR-31); the account can be deleted at any time.
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

MAX_FAVORITES = 10  # per kind


def user_data(user):
    return {"email": user.email, "nickname": user.nickname or user.email.split("@")[0],
            "is_staff": user.is_staff, "date_joined": user.date_joined}


def favorites_data(user):
    teams = user.favorite_teams.select_related("team").order_by("created_at")
    players = user.favorite_players.select_related("player").order_by("created_at")
    return {"teams": [{"code": f.team.code, "name": f.team.name, "gender": f.team.gender} for f in teams],
            "players": [{"code": f.player.code, "name": f.player.name, "gender": f.player.gender} for f in players]}


def session(user, code=status.HTTP_200_OK):
    token, _ = Token.objects.get_or_create(user=user)
    return Response({"token": token.key, "user": user_data(user), "favorites": favorites_data(user)}, status=code)


class SignupSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(max_length=128, trim_whitespace=False)
    nickname = serializers.CharField(max_length=20, min_length=2)

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("이미 가입된 이메일입니다.")
        return value

    def validate(self, attrs):
        probe = User(email=attrs["email"], username=attrs["email"], nickname=attrs["nickname"])
        try:
            password_validation.validate_password(attrs["password"], user=probe)
        except DjangoValidationError as e:
            raise serializers.ValidationError({"password": list(e.messages)})
        return attrs


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(max_length=128, trim_whitespace=False)


class AuthView(APIView):
    # no session/CSRF here: a stale admin session cookie must not block the token login
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
        except IntegrityError:  # two sign-ups with the same email at once
            return Response({"email": ["이미 가입된 이메일입니다."]}, status=status.HTTP_400_BAD_REQUEST)
        return session(user, status.HTTP_201_CREATED)


class LoginView(AuthView):
    """POST /api/auth/login {email, password} -> {token, user, favorites}"""

    def post(self, request):
        s = LoginSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        email = s.validated_data["email"].strip().lower()
        # accounts made with createsuperuser may have a username other than the email
        found = User.objects.filter(email__iexact=email).only("username").first()
        user = authenticate(request, username=found.username if found else email, password=s.validated_data["password"])
        if not user:
            # one message for both cases, so the form does not reveal which emails are registered
            return Response({"detail": "이메일 또는 비밀번호가 맞지 않습니다."}, status=status.HTTP_400_BAD_REQUEST)
        return session(user)


class LogoutView(APIView):
    """POST /api/auth/logout: deletes the token (signs out on every device)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    """GET /api/me: the signed-in user and favourites. DELETE /api/me {password}: delete the account."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"user": user_data(request.user), "favorites": favorites_data(request.user)})

    def delete(self, request):
        if not request.user.check_password(request.data.get("password") or ""):
            return Response({"password": ["비밀번호가 맞지 않습니다."]}, status=status.HTTP_400_BAD_REQUEST)
        request.user.delete()  # favourites and token go with it (CASCADE)
        return Response(status=status.HTTP_204_NO_CONTENT)


KINDS = {"team": (FavoriteTeam, Team, "team", "favorite_teams"),
         "player": (FavoritePlayer, Player, "player", "favorite_players")}


def _kind(kind):
    if kind not in KINDS:
        raise serializers.ValidationError({"kind": "team or player"})
    return KINDS[kind]


class FavoriteListView(APIView):
    """GET /api/me/favorites, POST /api/me/favorites {kind: team|player, code}"""

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
    """DELETE /api/me/favorites/{kind}/{code}"""

    permission_classes = [IsAuthenticated]

    def delete(self, request, kind, code):
        model, _, field, _ = _kind(kind)
        model.objects.filter(user=request.user, **{f"{field}__code": code}).delete()
        return Response(favorites_data(request.user))


class FeedView(APIView):
    """GET /api/me/feed: followed teams (record, last and next match) and players (last game, season so far)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(favorite_feed(request.user))
