from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Count, F, Sum, Window
from django.db.models.functions import Coalesce, RowNumber
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.images import process_image, serve_image
from moderation.permissions import restriction_for

from .models import Comment, CommentVote, Post, PostVote

SORTS = {"hot": "Hot", "new": "New", "top": "Top"}


def home(request):
    if not request.user.is_authenticated:
        return redirect("login")
    sort = request.GET.get("sort", "hot")
    if sort not in SORTS:
        sort = "hot"
    order = {"new": ("-created_at", "-id"), "top": ("-score", "-created_at", "-id")}.get(sort, ("-hot", "-id"))
    # stored rank/indexes: the database picks the 20 ids, then only those posts are loaded
    page = paged_ids(Post.objects.order_by(*order), 20, request.GET.get("page"),
                     Post.objects.select_related("author__profile"))
    votes = dict(PostVote.objects.filter(user=request.user, post__in=[p.pk for p in page])
                 .values_list("post_id", "value"))
    for post in page:
        post.my_vote = votes.get(post.pk, 0)
    return render(request, "feed/home.html", {"page": page, "sort": sort, "sorts": SORTS})


def _limited(request, flag, text):
    """True (and an error shown) when a moderator turned this feature off for the user."""
    if not getattr(restriction_for(request.user), flag):
        return False
    if request.headers.get("x-requested-with") != "fetch":
        messages.error(request, text)
    return True


NO_INTERACTION = "A moderator has turned off voting and commenting for your account."


@login_required
def new_post(request):
    if _limited(request, "no_posting", "A moderator has turned off posting for your account."):
        return redirect("home")
    errors = {}
    data = {"title": "", "body": ""}
    if request.method == "POST":
        data = {"title": request.POST.get("title", "").strip(), "body": request.POST.get("body", "").strip()}
        if not data["title"]:
            errors["title"] = "Give your post a title."
        elif len(data["title"]) > 200:
            errors["title"] = "Titles can be at most 200 characters."
        if len(data["body"]) > 10000:
            errors["body"] = "Posts can be at most 10,000 characters."
        image = None
        if request.FILES.get("image"):
            try:
                image = process_image(request.FILES["image"])
            except ValidationError as exc:
                errors["image"] = exc.messages[0]
        if not errors:
            post = Post(author=request.user, **data)
            if image:
                post.image.save(image.name, image, save=False)
            post.save()
            return redirect("post_detail", pk=post.pk)
    return render(request, "feed/new_post.html", {"data": data, "errors": errors})


def paged_ids(queryset, per_page, page_number, loader):
    """Page through `queryset` (ordered, no joins) by id only, which an index answers
    without sorting joined rows, then load just that page's objects with `loader`."""
    page = Paginator(queryset.values_list("id", flat=True), per_page).get_page(page_number)
    ids = list(page.object_list)
    objs = loader.in_bulk(ids)
    page.object_list = [objs[i] for i in ids if i in objs]
    return page


TOP_COMMENTS_PER_PAGE = 30   # top-level comments per page
COMMENT_BUDGET = 300         # most comments rendered on one page, replies included
MAX_DEPTH = 6                # deeper replies get a "continue this thread" link
REPLIES_PER_COMMENT = 10     # at first, so one popular comment can't use up the whole page


def _load_thread(post, roots):
    """Load replies under `roots` breadth-first, never more than COMMENT_BUDGET
    comments or MAX_DEPTH levels, so a post with 10,000 comments stays cheap."""
    ordering = ("-score", "created_at", "id")
    # replies are found by parent only: they're always on the same post, and filtering by
    # post as well steers MySQL to the post index and a scan of every comment on it
    base = Comment.objects.select_related("author__profile")
    for c in roots:
        c.children, c.depth = [], 0
    by_id = {c.pk: c for c in roots}
    frontier, budget = list(roots), COMMENT_BUDGET - len(roots)
    for depth in range(1, MAX_DEPTH + 1):
        if not frontier or budget <= 0:
            break
        ranked = base.filter(parent_id__in=[c.pk for c in frontier]).annotate(
            rank=Window(RowNumber(), partition_by=[F("parent_id")],
                        order_by=[F("score").desc(), F("created_at").asc(), F("id").asc()]))
        replies = list(ranked.filter(rank__lte=REPLIES_PER_COMMENT).order_by(*ordering)[:budget])
        budget -= len(replies)
        for r in replies:
            r.children, r.depth = [], depth
            by_id[r.parent_id].children.append(r)
            by_id[r.pk] = r
        frontier = replies
    # reply counts only for the comments on this page (one grouped query)
    n_replies = dict(Comment.objects.filter(parent_id__in=list(by_id)).values_list("parent_id")
                     .annotate(n=Count("id")).order_by())
    for c in by_id.values():
        c.n_replies = n_replies.get(c.pk, 0)
        c.hidden_replies = c.n_replies - len(c.children)   # "show N more replies"
        c.at_depth_limit = c.depth >= MAX_DEPTH
    return list(by_id.values())


@login_required
def post_detail(request, pk):
    post = get_object_or_404(Post.objects.select_related("author__profile"), pk=pk)
    thread_root = None
    base = Comment.objects.filter(post=post).select_related("author__profile")
    if request.GET.get("thread"):
        thread_root = get_object_or_404(base, pk=request.GET["thread"])
        roots, page = [thread_root], None
    else:
        top = Comment.objects.filter(post=post, parent__isnull=True).order_by("-score", "created_at", "id")
        page = paged_ids(top, TOP_COMMENTS_PER_PAGE, request.GET.get("cpage"), base)
        roots = list(page)
    loaded = _load_thread(post, roots)
    my_votes = dict(CommentVote.objects.filter(user=request.user, comment__in=[c.pk for c in loaded])
                    .values_list("comment_id", "value"))
    for c in loaded:
        c.my_vote = my_votes.get(c.pk, 0)
    vote = PostVote.objects.filter(user=request.user, post=post).first()
    post.my_vote = vote.value if vote else 0
    return render(request, "feed/post_detail.html", {"post": post, "tree": roots, "cpage": page,
                                                     "thread_root": thread_root})


@login_required
def post_image(request, pk):
    return serve_image(get_object_or_404(Post, pk=pk).image)


def _apply_vote(model, target_field, target, user, value):
    """Two quick taps can race to insert the same first vote; retry once on that collision."""
    try:
        return _apply_vote_once(model, target_field, target, user, value)
    except (IntegrityError, OperationalError):
        return _apply_vote_once(model, target_field, target, user, value)


def _apply_vote_once(model, target_field, target, user, value):
    with transaction.atomic():
        existing = model.objects.select_for_update().filter(user=user, **{target_field: target}).first()
        if existing and existing.value == value:
            existing.delete()  # clicking the same arrow again removes the vote
            mine = 0
        elif existing:
            existing.value = value
            existing.save(update_fields=["value"])
            mine = value
        else:
            model.objects.create(user=user, value=value, **{target_field: target})
            mine = value
        score = model.objects.filter(**{target_field: target}).aggregate(s=Coalesce(Sum("value"), 0))["s"]
        if isinstance(target, Post):
            Post.set_score(target.pk, score)
        else:
            type(target).objects.filter(pk=target.pk).update(score=score)
    return score, mine


def _forbidden(request, fallback):
    if request.headers.get("x-requested-with") == "fetch":
        return JsonResponse({"error": NO_INTERACTION}, status=403)
    return redirect(fallback)


def _vote_response(request, score, mine, fallback):
    if request.headers.get("x-requested-with") == "fetch":
        return JsonResponse({"score": score, "mine": mine})
    nxt = request.POST.get("next")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return redirect(nxt)
    return redirect(fallback)


@login_required
@require_POST
def vote_post(request, pk):
    post = get_object_or_404(Post, pk=pk)
    if _limited(request, "no_interaction", NO_INTERACTION):
        return _forbidden(request, reverse("post_detail", args=[pk]))
    try:
        value = int(request.POST.get("value", 0))
    except ValueError:
        value = 0
    if value not in (1, -1):
        return HttpResponseBadRequest()
    score, mine = _apply_vote(PostVote, "post", post, request.user, value)
    return _vote_response(request, score, mine, reverse("post_detail", args=[pk]))


@login_required
@require_POST
def vote_comment(request, pk):
    comment = get_object_or_404(Comment, pk=pk, is_deleted=False)
    if _limited(request, "no_interaction", NO_INTERACTION):
        return _forbidden(request, reverse("post_detail", args=[comment.post_id]))
    try:
        value = int(request.POST.get("value", 0))
    except ValueError:
        value = 0
    if value not in (1, -1):
        return HttpResponseBadRequest()
    score, mine = _apply_vote(CommentVote, "comment", comment, request.user, value)
    return _vote_response(request, score, mine, reverse("post_detail", args=[comment.post_id]) + f"#c{pk}")


@login_required
@require_POST
def add_comment(request, pk):
    post = get_object_or_404(Post, pk=pk)
    if _limited(request, "no_interaction", NO_INTERACTION):
        return redirect("post_detail", pk=pk)
    body = request.POST.get("body", "").strip()
    parent = None
    if request.POST.get("parent"):
        parent = get_object_or_404(Comment, pk=request.POST["parent"], post=post)
    if not body:
        messages.error(request, "Write something first.")
        return redirect("post_detail", pk=pk)
    if len(body) > 5000:
        messages.error(request, "Comments can be at most 5,000 characters.")
        return redirect("post_detail", pk=pk)
    comment = Comment.objects.create(post=post, author=request.user, parent=parent, body=body)
    Post.objects.filter(pk=pk).update(comment_count=F("comment_count") + 1)
    return redirect(reverse("post_detail", args=[pk]) + f"#c{comment.pk}")


@login_required
@require_POST
def delete_post(request, pk):
    post = get_object_or_404(Post, pk=pk)
    if post.author_id != request.user.pk:
        raise Http404
    if post.image:
        post.image.delete(save=False)
    post.delete()
    messages.success(request, "Post deleted.")
    return redirect("home")


@login_required
@require_POST
def delete_comment(request, pk):
    comment = get_object_or_404(Comment, pk=pk)
    if comment.author_id != request.user.pk:
        raise Http404
    if comment.is_deleted:  # double submit, or already removed by a moderator
        return redirect(reverse("post_detail", args=[comment.post_id]) + f"#c{pk}")
    # keep the row so replies stay threaded – reddit style "[deleted]"
    comment.is_deleted = True
    comment.body = ""
    comment.save(update_fields=["is_deleted", "body"])
    Post.objects.filter(pk=comment.post_id, comment_count__gt=0).update(comment_count=F("comment_count") - 1)
    return redirect(reverse("post_detail", args=[comment.post_id]) + f"#c{pk}")
