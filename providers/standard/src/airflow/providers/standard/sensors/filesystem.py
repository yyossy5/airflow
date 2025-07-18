#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
from __future__ import annotations

import datetime
import os
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property
from glob import glob
from typing import TYPE_CHECKING, Any

from airflow.configuration import conf
from airflow.exceptions import AirflowException
from airflow.providers.standard.hooks.filesystem import FSHook
from airflow.providers.standard.triggers.file import FileTrigger
from airflow.providers.standard.version_compat import BaseSensorOperator

try:
    from airflow.triggers.base import StartTriggerArgs
except ImportError:
    # TODO: Remove this when min airflow version is 2.10.0 for standard provider
    @dataclass
    class StartTriggerArgs:  # type: ignore[no-redef]
        """Arguments required for start task execution from triggerer."""

        trigger_cls: str
        next_method: str
        trigger_kwargs: dict[str, Any] | None = None
        next_kwargs: dict[str, Any] | None = None
        timeout: datetime.timedelta | None = None


if TYPE_CHECKING:
    try:
        from airflow.sdk.definitions.context import Context
    except ImportError:
        # TODO: Remove once provider drops support for Airflow 2
        from airflow.utils.context import Context


class FileSensor(BaseSensorOperator):
    """
    Waits for a file or folder to land in a filesystem.

    If the path given is a directory then this sensor will only return true if
    any files exist inside it (either directly, or within a subdirectory)

    :param fs_conn_id: reference to the File (path)
        connection id
    :param filepath: File or folder name (relative to
        the base path set within the connection), can be a glob.
    :param recursive: when set to ``True``, enables recursive directory matching behavior of
        ``**`` in glob filepath parameter. Defaults to ``False``.
    :param deferrable: If waiting for completion, whether to defer the task until done,
        default is ``False``.
    :param start_from_trigger: Start the task directly from the triggerer without going into the worker.
    :param trigger_kwargs: The keyword arguments passed to the trigger when start_from_trigger is set to True
        during dynamic task mapping. This argument is not used in standard usage.

    .. seealso::
        For more information on how to use this sensor, take a look at the guide:
        :ref:`howto/operator:FileSensor`


    """

    template_fields: Sequence[str] = ("filepath",)
    ui_color = "#91818a"
    start_trigger_args = StartTriggerArgs(
        trigger_cls="airflow.providers.standard.triggers.file.FileTrigger",
        trigger_kwargs={},
        next_method="execute_complete",
        next_kwargs=None,
        timeout=None,
    )
    start_from_trigger = False

    def __init__(
        self,
        *,
        filepath,
        fs_conn_id="fs_default",
        recursive=False,
        deferrable: bool = conf.getboolean("operators", "default_deferrable", fallback=False),
        start_from_trigger: bool = False,
        trigger_kwargs: dict[str, Any] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.filepath = filepath
        self.fs_conn_id = fs_conn_id
        self.recursive = recursive
        self.deferrable = deferrable

        self.start_from_trigger = start_from_trigger

        if self.deferrable and self.start_from_trigger:
            self.start_trigger_args.timeout = datetime.timedelta(seconds=self.timeout)
            self.start_trigger_args.trigger_kwargs = dict(
                filepath=self.path,
                recursive=self.recursive,
                poke_interval=self.poke_interval,
            )

    @cached_property
    def path(self) -> str:
        hook = FSHook(self.fs_conn_id)
        basepath = hook.get_path()
        full_path = os.path.join(basepath, self.filepath)
        return full_path

    def poke(self, context: Context) -> bool:
        self.log.info("Poking for file %s", self.path)
        for path in glob(self.path, recursive=self.recursive):
            if os.path.isfile(path):
                mod_time = datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y%m%d%H%M%S")
                self.log.info("Found File %s last modified: %s", path, mod_time)
                return True

            for _, _, files in os.walk(path):
                if files:
                    return True
        return False

    def execute(self, context: Context) -> None:
        if not self.deferrable:
            super().execute(context=context)
        if not self.poke(context=context):
            self.defer(
                timeout=datetime.timedelta(seconds=self.timeout),
                trigger=FileTrigger(
                    filepath=self.path,
                    recursive=self.recursive,
                    poke_interval=self.poke_interval,
                ),
                method_name="execute_complete",
            )

    def execute_complete(self, context: Context, event: bool | None = None) -> None:
        if not event:
            raise AirflowException("%s task failed as %s not found.", self.task_id, self.filepath)
        self.log.info("%s completed successfully as %s found.", self.task_id, self.filepath)
