from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32, pattern=r'^[A-Za-z0-9._-]+$')]


class FavoriteGroup(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1, max_length=100)
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
    codes: list[Code] = Field(default_factory=list, max_length=1000)


class FavoriteLibraryData(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(default=0, ge=0)
    codes: list[Code] = Field(default_factory=list, max_length=1000)
    groups: list[FavoriteGroup] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def validate_library(self):
        self.codes = list(dict.fromkeys(self.codes))
        if len({g.id for g in self.groups}) != len(self.groups) or len({g.name for g in self.groups}) != len(self.groups):
            raise ValueError('그룹 이름과 ID는 중복될 수 없습니다.')
        for group in self.groups:
            if not set(group.codes).issubset(self.codes):
                raise ValueError('그룹 종목은 관심종목에 포함되어야 합니다.')
            group.codes = list(dict.fromkeys(group.codes))
        return self
