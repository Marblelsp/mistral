# -*- coding: utf-8 -*-
#
# Copyright 2015 - Mirantis, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

import copy
import datetime
import json
import mock

from mistral.db.v2 import api as db_api
from mistral.db.v2.sqlalchemy import models
from mistral.engine import rpc
from mistral import exceptions as exc
from mistral.tests.unit.api import base
from mistral.workflow import states
from mistral.workflow import utils as wf_utils


ACTION_EX_DB = models.ActionExecution(
    id='123',
    workflow_name='flow',
    task_execution=models.TaskExecution(name='task1'),
    task_execution_id='333',
    state=states.SUCCESS,
    state_info=states.SUCCESS,
    tags=['foo', 'fee'],
    name='std.echo',
    description='something',
    accepted=True,
    input={},
    output={},
    created_at=datetime.datetime(1970, 1, 1),
    updated_at=datetime.datetime(1970, 1, 1)
)

ACTION_EX = {
    'id': '123',
    'workflow_name': 'flow',
    'task_execution_id': '333',
    'task_name': 'task1',
    'state': 'SUCCESS',
    'state_info': 'SUCCESS',
    'tags': ['foo', 'fee'],
    'name': 'std.echo',
    'description': 'something',
    'accepted': True,
    'input': '{}',
    'output': '{}',
    'created_at': '1970-01-01 00:00:00',
    'updated_at': '1970-01-01 00:00:00'
}

UPDATED_ACTION_EX_DB = copy.copy(ACTION_EX_DB).to_dict()
UPDATED_ACTION_EX_DB['state'] = 'SUCCESS'
UPDATED_ACTION_EX_DB['task_name'] = 'task1'
UPDATED_ACTION = copy.copy(ACTION_EX)
UPDATED_ACTION['state'] = 'SUCCESS'
UPDATED_ACTION_OUTPUT = UPDATED_ACTION['output']

ERROR_ACTION_EX = copy.copy(ACTION_EX_DB).to_dict()
ERROR_ACTION_EX['state'] = 'ERROR'
ERROR_ACTION_EX['task_name'] = 'task1'
ERROR_ACTION = copy.copy(ACTION_EX)
ERROR_ACTION['state'] = 'ERROR'
ERROR_ACTION_RES = ERROR_ACTION['output']

BROKEN_ACTION = copy.copy(ACTION_EX)
BROKEN_ACTION['output'] = 'string not escaped'

MOCK_ACTION = mock.MagicMock(return_value=ACTION_EX_DB)
MOCK_ACTIONS = mock.MagicMock(return_value=[ACTION_EX_DB])
MOCK_EMPTY = mock.MagicMock(return_value=[])
MOCK_NOT_FOUND = mock.MagicMock(side_effect=exc.NotFoundException())


class TestActionExecutionsController(base.FunctionalTest):
    @mock.patch.object(db_api, 'get_action_execution', MOCK_ACTION)
    def test_get(self):
        resp = self.app.get('/v2/action_executions/123')

        self.assertEqual(resp.status_int, 200)
        self.assertDictEqual(ACTION_EX, resp.json)

    @mock.patch.object(db_api, 'get_action_execution', MOCK_NOT_FOUND)
    def test_get_not_found(self):
        resp = self.app.get('/v2/action_executions/123', expect_errors=True)

        self.assertEqual(resp.status_int, 404)

    @mock.patch.object(rpc.EngineClient, 'start_action')
    def test_post(self, f):
        f.return_value = ACTION_EX_DB.to_dict()

        resp = self.app.post_json(
            '/v2/action_executions',
            {
                'name': 'std.echo',
                'input': "{}",
                'params': '{"save_result": true}'
            }
        )

        self.assertEqual(resp.status_int, 201)

        action_exec = ACTION_EX
        del action_exec['task_name']

        self.assertDictEqual(ACTION_EX, resp.json)

        f.assert_called_once_with(
            ACTION_EX['name'],
            json.loads(ACTION_EX['input']),
            description=None,
            save_result=True
        )

    @mock.patch.object(rpc.EngineClient, 'start_action')
    def test_post_without_input(self, f):
        f.return_value = ACTION_EX_DB.to_dict()
        f.return_value['output'] = {'result': '123'}

        resp = self.app.post_json(
            '/v2/action_executions',
            {'name': 'nova.servers_list'}
        )

        self.assertEqual(resp.status_int, 201)
        self.assertEqual('{"result": "123"}', resp.json['output'])

        f.assert_called_once_with('nova.servers_list', {}, description=None)

    def test_post_bad_result(self):
        resp = self.app.post_json(
            '/v2/action_executions',
            {'input': 'null'},
            expect_errors=True
        )

        self.assertEqual(resp.status_int, 400)

    def test_post_bad_input(self):
        resp = self.app.post_json(
            '/v2/action_executions',
            {'input': None},
            expect_errors=True
        )

        self.assertEqual(resp.status_int, 400)

    @mock.patch.object(rpc.EngineClient, 'on_action_complete')
    def test_put(self, f):
        f.return_value = UPDATED_ACTION_EX_DB

        resp = self.app.put_json('/v2/action_executions/123', UPDATED_ACTION)

        self.assertEqual(resp.status_int, 200)
        self.assertDictEqual(UPDATED_ACTION, resp.json)

        f.assert_called_once_with(
            UPDATED_ACTION['id'],
            wf_utils.Result(data=ACTION_EX_DB.output)
        )

    @mock.patch.object(rpc.EngineClient, 'on_action_complete')
    def test_put_error(self, f):
        f.return_value = ERROR_ACTION_EX

        resp = self.app.put_json('/v2/action_executions/123', ERROR_ACTION)

        self.assertEqual(resp.status_int, 200)
        self.assertDictEqual(ERROR_ACTION, resp.json)

        f.assert_called_once_with(
            ERROR_ACTION['id'],
            wf_utils.Result(error=ACTION_EX_DB.output)
        )

    @mock.patch.object(
        rpc.EngineClient,
        'on_action_complete',
        MOCK_NOT_FOUND
    )
    def test_put_no_action_ex(self):
        resp = self.app.put_json(
            '/v2/action_executions/123',
            UPDATED_ACTION,
            expect_errors=True
        )

        self.assertEqual(resp.status_int, 404)

    def test_put_bad_result(self):
        resp = self.app.put_json(
            '/v2/action_executions/123',
            BROKEN_ACTION,
            expect_errors=True
        )

        self.assertEqual(resp.status_int, 400)

    @mock.patch.object(rpc.EngineClient, 'on_action_complete')
    def test_put_without_result(self, f):
        action_ex = copy.copy(UPDATED_ACTION)
        del action_ex['output']

        f.return_value = UPDATED_ACTION_EX_DB

        resp = self.app.put_json('/v2/action_executions/123', action_ex)

        self.assertEqual(resp.status_int, 200)

    @mock.patch.object(db_api, 'get_action_executions', MOCK_ACTIONS)
    def test_get_all(self):
        resp = self.app.get('/v2/action_executions')

        self.assertEqual(resp.status_int, 200)

        self.assertEqual(len(resp.json['action_executions']), 1)
        self.assertDictEqual(ACTION_EX, resp.json['action_executions'][0])

    @mock.patch.object(db_api, 'get_action_executions', MOCK_EMPTY)
    def test_get_all_empty(self):
        resp = self.app.get('/v2/action_executions')

        self.assertEqual(resp.status_int, 200)

        self.assertEqual(len(resp.json['action_executions']), 0)
