"""Every workflow the worker runs, in one list: the worker registers them, and the replay tests
replay every recorded history against them (tests/histories/)."""

from .background import TellChatWorkflow
from .deletion import (
    DeleteAccountWorkflow,
    DeleteThreadWorkflow,
    SweepDeletedUsersWorkflow,
)
from .directory import SyncDirectoryWorkflow
from .environment import EnvironmentWorkflow, RefreshEnvironmentsWorkflow
from .plugins import SyncPluginSourcesWorkflow, SyncPluginSourceWorkflow
from .runs import RunWorkflow
from .search import ReindexSearchWorkflow
from .tasks import TaskFiringWorkflow

ALL_WORKFLOWS = [
    RunWorkflow,
    DeleteThreadWorkflow,
    DeleteAccountWorkflow,
    SweepDeletedUsersWorkflow,
    ReindexSearchWorkflow,
    SyncDirectoryWorkflow,
    EnvironmentWorkflow,
    RefreshEnvironmentsWorkflow,
    SyncPluginSourceWorkflow,
    SyncPluginSourcesWorkflow,
    TaskFiringWorkflow,
    TellChatWorkflow,
]
