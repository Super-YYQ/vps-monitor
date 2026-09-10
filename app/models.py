from typing import Literal
from urllib.parse import urlsplit

import soupsieve
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def public_url(value: str) -> str:
    if not value:
        return value
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("请输入不含账号密码的 HTTP / HTTPS 地址")
    if parsed.port not in {None, 80, 443} or len(value) > 2048:
        raise ValueError("只允许 80 / 443 端口，地址不超过 2048 字符")
    return value


class Monitor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    url: str = ""
    purchase_url: str = ""
    adapter: Literal["html", "whmcs", "json"] = "html"
    selector: str = Field(default="", max_length=300)
    product_match: str = Field(default="", max_length=160)
    expected_text: str = Field(default="", max_length=160)
    mirror_aliases: list[str] = Field(default_factory=list, max_length=10)
    in_stock: list[str] = Field(default_factory=lambda: ["In Stock", "有货"], max_length=20)
    out_of_stock: list[str] = Field(
        default_factory=lambda: ["Out of Stock", "Sold Out", "缺货", "售罄"], max_length=20
    )
    json_path: str = Field(default="", max_length=200)
    interval: int = Field(default=120, ge=30, le=86400)
    confirmations: int = Field(default=2, ge=1, le=5)
    enabled: bool = False
    notify: bool = True
    notify_initial: bool = False
    region: str = Field(default="Los Angeles", max_length=100)
    specs: str = Field(default="", max_length=800)
    price_note: str = Field(default="", max_length=160)
    notes: str = Field(default="", max_length=1500)

    _urls = field_validator("url", "purchase_url")(public_url)

    @field_validator("selector")
    @classmethod
    def selector_valid(cls, value):
        if value:
            try:
                soupsieve.compile(value)
            except Exception as exc:
                raise ValueError("CSS 选择器语法错误") from exc
        return value

    @field_validator("in_stock", "out_of_stock")
    @classmethod
    def terms_valid(cls, value):
        if not value or any(not term.strip() or len(term) > 200 for term in value):
            raise ValueError("库存标记不能为空，每个不超过 200 字符")
        return [term.strip() for term in value]

    @field_validator("mirror_aliases")
    @classmethod
    def aliases_valid(cls, value):
        if any(not alias.strip() or len(alias) > 160 for alias in value):
            raise ValueError("镜像别名不能为空，每个不超过 160 字符")
        return [alias.strip() for alias in value]

    @model_validator(mode="after")
    def configuration_valid(self):
        if self.enabled and not self.url:
            raise ValueError("启用前请填写检测地址")
        if self.enabled and self.adapter == "html" and not self.selector:
            raise ValueError("HTML 检测必须填写单个产品的 CSS 选择器")
        if self.enabled and self.adapter == "whmcs" and not self.expected_text:
            raise ValueError("WHMCS 检测请填写预期产品名称")
        if self.enabled and self.adapter == "json" and not self.json_path:
            raise ValueError("JSON 检测请填写库存字段路径")
        if set(x.casefold() for x in self.in_stock) & set(x.casefold() for x in self.out_of_stock):
            raise ValueError("有货和缺货标记不能相同")
        return self


class MailSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    host: str = Field(default="", max_length=253, pattern=r"^[a-zA-Z0-9.\-]*$")
    port: int = Field(default=587, ge=1, le=65535)
    security: Literal["starttls", "ssl"] = "starttls"
    username: str = Field(default="", max_length=254)
    password: str = Field(default="", max_length=500)
    sender: str = ""
    recipients: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_mail(self):
        import re

        for address in [self.sender, *self.recipients]:
            if address and not re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", address):
                raise ValueError("请输入完整邮箱地址，不要包含显示名")
        if self.enabled and (not self.host or not self.sender or not self.recipients):
            raise ValueError("启用邮件通知前请填写服务器、发件人和收件人")
        return self


class MailProfile(MailSettings):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def profile_name(cls, value):
        if not value.strip():
            raise ValueError("请输入配置名称")
        return value.strip()
