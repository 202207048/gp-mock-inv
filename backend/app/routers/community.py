"""
routers/community.py - 커뮤니티 게시판 API 엔드포인트

제공하는 API:
    GET    /community/posts              → 게시글 목록 (페이징 지원)
    POST   /community/posts              → 게시글 작성
    GET    /community/posts/{id}         → 게시글 상세 (댓글 포함)
    DELETE /community/posts/{id}         → 게시글 삭제 (본인만)
    POST   /community/posts/{id}/comments → 댓글 작성
    POST   /community/posts/{id}/like   → 좋아요 토글

페이징(Paging)이란?
    데이터가 많을 때 한 번에 다 가져오지 않고
    페이지 단위로 나눠서 가져오는 방법입니다.
    예: 전체 100개 게시글을 page=1&size=20 으로 요청하면 1~20번만 반환
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.community import Comment, Post, PostLike
from app.models.user import User
from app.schemas.community import CommentCreateRequest, PostCreateRequest
from app.utils.deps import get_current_user

# prefix는 main.py에서 /api/community 로 지정
# 최종 경로 예시: /api/community/posts, /api/community/posts/1/like
router = APIRouter(tags=["커뮤니티"])


@router.get("/posts", summary="게시글 목록")
def get_posts(
    symbol_code: str | None = Query(None, description="종목별 필터 (없으면 전체)"),
    page: int = Query(1, ge=1, description="페이지 번호 (1부터 시작)"),
    size: int = Query(20, ge=1, le=100, description="페이지당 게시글 수"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    게시글 목록을 페이지 단위로 반환합니다.
    
    사용 예시:
        GET /community/posts              → 전체 게시글 1페이지 (20개)
        GET /community/posts?page=2       → 2페이지
        GET /community/posts?symbol_code=005930 → 삼성전자 종목 게시판
    
    응답 예시:
        {
            "total": 150,
            "page": 1,
            "items": [
                {
                    "post_id": 1,
                    "title": "삼성전자 어떻게 생각하세요?",
                    "author_name": "홍길동",
                    "like_count": 5,
                    "comment_count": 3,
                    ...
                }
            ]
        }
    """
    query = db.query(Post)

    # 종목 코드로 필터링 (종목별 게시판)
    if symbol_code:
        query = query.filter(Post.symbol_code == symbol_code)

    # 전체 게시글 수 (페이징 계산용)
    total = query.count()

    # 페이징 처리
    # offset: 건너뛸 행 수 (2페이지면 20개 건너뜀)
    # limit: 가져올 행 수
    posts = query.order_by(Post.created_at.desc()).offset((page - 1) * size).limit(size).all()

    return {
        "total": total,
        "page": page,
        "items": [
            {
                "post_id": p.post_id,
                "title": p.title,
                # p.author: Post 모델의 relationship으로 User 객체 바로 접근
                "author_name": p.author.user_name if p.author else "",
                "symbol_code": p.symbol_code,
                "created_at": p.created_at,
                "like_count": len(p.likes),       # 좋아요 수
                "comment_count": len(p.comments), # 댓글 수
            }
            for p in posts
        ],
    }


@router.post("/posts", summary="게시글 작성")
def create_post(
    req: PostCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    새 게시글을 작성합니다.
    
    요청 예시:
        POST /community/posts
        {
            "title": "삼성전자 어떻게 생각하세요?",
            "content": "요즘 반도체 업황이...",
            "symbol_code": "005930"   # 종목 게시판에 올릴 경우 (선택사항)
        }
    """
    post = Post(
        user_id=current_user.user_id,
        title=req.title,
        content=req.content,
        symbol_code=req.symbol_code,  # None이면 자유게시판
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return {"post_id": post.post_id, "message": "게시글이 등록되었습니다."}


@router.get("/posts/{post_id}", summary="게시글 상세")
def get_post(
    post_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    게시글 상세 내용과 댓글 목록을 반환합니다.
    """
    post = db.query(Post).filter(Post.post_id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="게시글을 찾을 수 없습니다.")

    return {
        "post_id": post.post_id,
        "title": post.title,
        "content": post.content,
        "author_name": post.author.user_name if post.author else "",
        "symbol_code": post.symbol_code,
        "created_at": post.created_at,
        "like_count": len(post.likes),
        # 댓글 목록도 함께 반환
        "comments": [
            {
                "comment_id": c.comment_id,
                "author_name": c.author.user_name if c.author else "",
                "content": c.content,
                "created_at": c.created_at,
            }
            for c in post.comments
        ],
    }


@router.delete("/posts/{post_id}", summary="게시글 삭제")
def delete_post(
    post_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    게시글을 삭제합니다. 본인이 작성한 게시글만 삭제 가능합니다.
    
    cascade 설정으로 댓글과 좋아요도 자동으로 삭제됩니다.
    """
    # post_id와 user_id를 동시에 조건으로 → 다른 사람 게시글 삭제 방지
    post = db.query(Post).filter(
        Post.post_id == post_id,
        Post.user_id == current_user.user_id,
    ).first()

    if not post:
        raise HTTPException(status_code=404, detail="게시글을 찾을 수 없거나 권한이 없습니다.")

    db.delete(post)
    db.commit()
    return {"message": "삭제되었습니다."}


@router.post("/posts/{post_id}/comments", summary="댓글 작성")
def create_comment(
    post_id: int,
    req: CommentCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    게시글에 댓글을 작성합니다.
    
    요청 예시:
        POST /community/posts/1/comments
        {"content": "좋은 의견이네요!"}
    """
    # 게시글이 존재하는지 먼저 확인
    post = db.query(Post).filter(Post.post_id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="게시글을 찾을 수 없습니다.")

    comment = Comment(
        post_id=post_id,
        user_id=current_user.user_id,
        content=req.content,
    )
    db.add(comment)
    db.commit()
    return {"message": "댓글이 등록되었습니다."}


@router.post("/posts/{post_id}/like", summary="좋아요 토글")
def toggle_like(
    post_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    게시글 좋아요를 토글합니다.
    
    - 좋아요 안 한 상태에서 누르면 → 좋아요 추가
    - 이미 좋아요 한 상태에서 누르면 → 좋아요 취소
    
    응답:
        {"liked": true, "message": "좋아요!"}       → 추가됨
        {"liked": false, "message": "좋아요가 취소되었습니다."} → 취소됨
    """
    # 이미 좋아요를 눌렀는지 확인
    existing = db.query(PostLike).filter(
        PostLike.post_id == post_id,
        PostLike.user_id == current_user.user_id,
    ).first()

    if existing:
        # 이미 좋아요 → 취소
        db.delete(existing)
        db.commit()
        return {"liked": False, "message": "좋아요가 취소되었습니다."}

    # 좋아요 추가
    like = PostLike(post_id=post_id, user_id=current_user.user_id)
    db.add(like)
    db.commit()
    return {"liked": True, "message": "좋아요!"}
