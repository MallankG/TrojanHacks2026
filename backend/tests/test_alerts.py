from app.schemas import AlertAccountLink, AlertTransactionLink
from app.services.alerts import ACCOUNT_ROLE_RANK, TXN_ROLE_RANK, _strongest_roles

T = "22222222-2222-2222-2222-222222222222"


def test_duplicate_links_collapse_to_strongest_role():
    links = [AlertTransactionLink(transaction_id=T, role="supporting"), AlertTransactionLink(transaction_id=T, role="trigger")]
    assert _strongest_roles(links, "transaction_id", TXN_ROLE_RANK) == {T: "trigger"}
    accounts = [AlertAccountLink(account_id=T, role="counterparty"), AlertAccountLink(account_id=T, role="intermediary")]
    assert _strongest_roles(accounts, "account_id", ACCOUNT_ROLE_RANK) == {T: "intermediary"}
