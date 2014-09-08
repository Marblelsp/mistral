# Copyright 2014 - Mirantis, Inc.
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

import inspect

from stevedore import extension

from mistral.actions import action_factory
from mistral.actions import generator_factory
from mistral.actions import std_actions
from mistral.db.v2 import api as db_api
from mistral import exceptions as exc
from mistral import expressions as expr
from mistral.openstack.common import log as logging
from mistral.utils import inspect_utils as i_utils
from mistral.workbook import parser as spec_parser


LOG = logging.getLogger(__name__)

_ACTION_CTX_PARAM = 'action_context'


def get_registered_actions(**kwargs):
    return db_api.get_actions(**kwargs)


def _register_action_in_db(name, action_class, attributes,
                           description=None):
    values = {
        'name': name,
        'action_class': action_class,
        'attributes': attributes,
        'description': description,
        'is_system': True
    }

    try:
        LOG.debug("Registering action in DB: %s" % name)

        db_api.create_action(values)
    except exc.DBDuplicateEntry:
        LOG.debug("Action %s already exists in DB." % name)


def _clear_system_action_db():
    db_api.delete_actions(is_system=True)


def sync_db():
    _clear_system_action_db()
    register_action_classes()


def _register_dynamic_action_classes():
    for generator in generator_factory.all_generators():
        action_classes = generator.create_action_classes()

        module = generator.base_action_class.__module__
        class_name = generator.base_action_class.__name__

        action_class_str = "%s.%s" % (module, class_name)

        for action_name, action in action_classes.items():
            full_action_name =\
                "%s.%s" % (generator.action_namespace, action_name)

            attrs = i_utils.get_public_fields(action)

            _register_action_in_db(
                full_action_name,
                action_class_str,
                attrs
            )


def register_action_classes():
    mgr = extension.ExtensionManager(
        namespace='mistral.actions',
        invoke_on_load=False
    )

    with db_api.transaction():
        for name in mgr.names():
            action_class_str = mgr[name].entry_point_target.replace(':', '.')
            attrs = i_utils.get_public_fields(mgr[name].plugin)

            _register_action_in_db(name, action_class_str, attrs)

        _register_dynamic_action_classes()


def get_action_db(action_name):
    return db_api.load_action(action_name)


def get_action_class(action_full_name):
    """Finds action class by full action name (i.e. 'namespace.action_name').

    :param action_full_name: Full action name (that includes namespace).
    :return: Action class or None if not found.
    """
    action_db = get_action_db(action_full_name)

    if action_db:
        return action_factory.construct_action_class(action_db.action_class,
                                                     action_db.attributes)


def _get_action_context(db_task, openstack_context):
    result = {
        'workbook_name': db_task['workbook_name'],
        'execution_id': db_task['execution_id'],
        'task_id': db_task['id'],
        'task_name': db_task['name'],
        'task_tags': db_task['tags'],
    }

    if openstack_context:
        result.update({'openstack': openstack_context})

    return result


def _has_action_context_param(action_cls):
    arg_spec = inspect.getargspec(action_cls.__init__)

    return _ACTION_CTX_PARAM in arg_spec.args


# TODO(rakhmerov): It's not used anywhere.
def _create_adhoc_action(db_task, openstack_context):
    task_spec = spec_parser.get_task_spec(db_task['task_spec'])

    full_action_name = task_spec.get_full_action_name()

    raw_action_spec = db_task['action_spec']

    if not raw_action_spec:
        return None

    action_spec = spec_parser.get_action_spec(raw_action_spec)

    LOG.info('Using ad-hoc action [action=%s, db_task=%s]' %
             (full_action_name, db_task))

    # Create an ad-hoc action.
    base_cls = get_action_class(action_spec.clazz)

    action_context = None
    if _has_action_context_param(base_cls):
        action_context = _get_action_context(db_task, openstack_context)

    if not base_cls:
        msg = 'Ad-hoc action base class is not registered ' \
              '[workbook_name=%s, action=%s, base_class=%s]' % \
              (db_task['workbook_name'], full_action_name, base_cls)
        raise exc.ActionException(msg)

    action_params = db_task['parameters'] or {}

    return std_actions.AdHocAction(action_context,
                                   base_cls,
                                   action_spec,
                                   **action_params)


# TODO(rakhmerov): It's not used anywhere. Remove it later.
def create_action(db_task):
    task_spec = spec_parser.get_task_spec(db_task['task_spec'])

    full_action_name = task_spec.get_full_action_name()

    action_cls = get_action_class(full_action_name)

    openstack_ctx = db_task['in_context'].get('openstack')

    if not action_cls:
        # If action is not found in registered actions try to find ad-hoc
        # action definition.
        if openstack_ctx is not None:
            db_task['parameters'].update({'openstack': openstack_ctx})
        action = _create_adhoc_action(db_task, openstack_ctx)

        if action:
            return action
        else:
            msg = 'Unknown action [workbook_name=%s, action=%s]' % \
                  (db_task['workbook_name'], full_action_name)
            raise exc.ActionException(msg)

    action_params = db_task['parameters'] or {}

    if _has_action_context_param(action_cls):
        action_params[_ACTION_CTX_PARAM] = _get_action_context(db_task,
                                                               openstack_ctx)

    try:
        return action_cls(**action_params)
    except Exception as e:
        raise exc.ActionException('Failed to create action [db_task=%s]: %s' %
                                  (db_task, e))


def resolve_adhoc_action_name(workbook, action_name):
    action_spec = workbook.get_action(action_name)

    if not action_spec:
        msg = 'Ad-hoc action class is not registered ' \
              '[workbook=%s, action=%s, action_spec=%s]' % \
              (workbook, action_name, action_spec)
        raise exc.ActionException(msg)

    base_cls = get_action_class(action_spec.clazz)

    if not base_cls:
        msg = 'Ad-hoc action base class is not registered ' \
              '[workbook=%s, action=%s, base_class=%s]' % \
              (workbook, action_name, base_cls)
        raise exc.ActionException(msg)

    return action_spec.clazz


def convert_adhoc_action_params(workbook, action_name, params):
    base_params = workbook.get_action(action_name).base_parameters

    if not base_params:
        return {}

    return expr.evaluate_recursively(base_params, params)


def convert_adhoc_action_result(workbook, action_name, result):
    transformer = workbook.get_action(action_name).output

    if not transformer:
        return result

    # Use base action result as a context for evaluating expressions.
    return expr.evaluate_recursively(transformer, result)
