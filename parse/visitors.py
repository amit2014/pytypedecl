# -*- coding:utf-8; python-indent:2; indent-tabs-mode:nil -*-

# Copyright 2013 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Visitor(s) for walking ASTs."""

# pylint: disable=protected-access
# pylint: disable=g-importing-member

import re
from .. import pytd


class PrintVisitor(object):
  """Visitor for converting ASTs back to pytd source code."""

  INDENT = " "*4
  VALID_NAME = re.compile(r"^[a-zA-Z_]\w*$")

  def SafeName(self, name):
    if not self.VALID_NAME.match(name):
      # We can do this because name will never contain backticks. Everything
      # we process here came in through the pytd parser, and the pytd syntax
      # doesn't allow escaping backticks themselves.
      return "`%s`" % name
    else:
      return name

  def VisitTypeDeclUnit(self, node):
    """Convert the AST for an entire pytd file back to a string."""
    sections = ("\n".join(section)
                for section in [node.constants, node.functions, node.classes]
                if section)
    return "\n\n".join(sections)

  def VisitConstant(self, node):
    """Convert a class-level or module-level constant to a string."""
    return node.name + ": " + node.type

  def VisitClass(self, node):
    """Visit a class, producing a string.

    class name<template>(parents....):
      constants...
      methods...

    Args:
      node: class node
    Returns:
      string representation of this class
    """
    parents = "(" + ", ".join(node.parents) + ")" if node.parents else ""
    template = "<" + ", ".join(node.template) + ">" if node.template else ""
    constants = [self.INDENT + m for m in node.constants]
    if node.methods:
      # We have multiple methods, and every method has multiple signatures
      # (i.e., the method string will have multiple lines). Combine this into
      # an array that contains all the lines, then indent the result.
      all_lines = sum((m.splitlines() for m in node.methods), [])
      methods = [self.INDENT + m for m in all_lines]
    else:
      methods = [self.INDENT + "pass"]
    header = "class " + self.SafeName(node.name) + template + parents + ":"
    return "\n".join([header] + constants + methods) + "\n"

  def VisitFunction(self, node):
    """Visit a function, producing a multi-line string (one for each signature).

    E.g.:
      def multiply(x:int, y:int) -> int
      def multiply(x:float, y:float) -> float

    Args:
      node: A function node.
    Returns:
      string representation of the function.
    """
    return "\n".join("def " + node.name + sig for sig in node.signatures)

  def VisitSignature(self, node):
    """Visit a signature, producing a string.

    E.g.:
      (x: int, y: int, z: unicode) -> str raises ValueError

    Args:
      node: signature node
    Returns:
      string representation of the signature (no "def" and function name)
    """
    # TODO: template

    # Potentially abbreviate. "object" is the default.
    ret = " -> " + node.return_type if node.return_type != "object" else ""

    exc = " raises " + ", ".join(node.exceptions) if node.exceptions else ""
    optional = ("...",) if node.has_optional else ()
    return "(" + ", ".join(node.params + optional) + ")" + ret + exc

  def VisitParameter(self, node):
    """Convert a template parameter to a string."""
    if node.type != "object":
      return node.name + ": " + node.type
    else:
      # Abbreviated form. "object" is the default.
      return node.name

  def VisitTemplateItem(self, node):
    """Convert a template (E.g. "<X extends list>") to a string."""
    return node.name + "<" + node.within_type + ">"

  def VisitBasicType(self, node):
    """Convert a type to a string."""
    return self.SafeName(node.containing_type)

  def VisitNativeType(self, node):
    """Convert a native type to a string."""
    return self.SafeName(node.python_type.__name__)

  def VisitClassType(self, node):
    return self.SafeName(node.cls.name)

  def VisitHomogeneousContainerType(self, node):
    """Convert a homogeneous container type to a string."""
    return node.base_type + "<" + node.element_type + ">"

  def VisitGenericType(self, node):
    """Convert a generic type (E.g. list<int>) to a string."""
    return node.base_type + "<" + ", ".join(p for p in node.parameters) + ">"

  def VisitUnionType(self, node):
    """Convert a union type ("x or y") to a string."""
    # TODO: insert parentheses if necessary (i.e., if the parent is
    # an intersection.)
    return " or ".join(node.type_list)

  def VisitIntersectionType(self, node):
    """Convert an intersection type ("x and y") to a string."""
    return " and ".join(node.type_list)


class StripSelf(object):
  """Transforms the tree into one where methods don't have the "self" parameter.

  This is useful for certain kinds of postprocessing and testing.
  """

  def VisitClass(self, node):
    """Visits a Class, and removes "self" from all its methods."""
    return node._replace(methods=[self._StripFunction(m)
                                  for m in node.methods])

  def _StripFunction(self, node):
    """Remove "self" from all signatures of a method."""
    return node._replace(signatures=tuple(self.StripSignature(s)
                                          for s in node.signatures))

  def StripSignature(self, node):
    """Remove "self" from a Signature. Assumes "self" is the first argument."""
    return node._replace(params=node.params[1:])


class FillInClasses(object):
  """Fill in ClassType pointers using a symbol table.

  This is an in-place visitor! It modifies the original tree. This is
  necessary because we introduce loops.
  """

  def __init__(self, symbol_table):
    """Create this visitor.

    You're expected to then pass this instance to node.Visit().

    Args:
      symbol_table: The symbol table to use for looking up classes.
    """
    self.symbol_table = symbol_table

  def VisitTypeDeclUnit(self, unused_unit):
    """Visits a top level module. This is usually this visitor's entry point.

    Args:
      unused_unit: Our module. (ignored)

    Returns:
      Explicitly returns None, to trip any callers treating this visitor as
      a pure function.
    """
    return None

  def VisitClassType(self, node):
    """Fills in a class type.

    Args:
      node: A ClassType. This node will have a name, which we use for lookup.

    Returns:
      The same ClassType. We will have filled in its "cls" attribute.

    Raises:
      KeyError: If we can't find a given class.
    """
    node.cls = self.symbol_table.Lookup(node.name)
    return node


class LookupClasses(object):
  """Change all NamedType objects to ClassType objects, by looking them up.

  This is a destructive visitor! It modifies the original tree. This is
  necessary because we introduce cycles to the tree.
  """

  def VisitTypeDeclUnit(self, unit):
    """Converts a module from one using BasicType to ClassType.

    Args:
      unit: The module to process.

    Returns:
      A new module that only uses ClassType. All ClassType instances will point
      to concrete classes.

    Throws:
      KeyError: If we can't find a class by this name.
    """
    # Since the node visitor protocol calls us *after* our children, the classes
    # will already have been processed (i.e., contain ClassType instead of
    # BasicType).
    unit.Visit(FillInClasses(unit))  # discard return value (None)
    return unit

  def VisitBasicType(self, node):
    """Converts a named type to a class type, to be filled in later.

    Args:
      node: The BasicType. This type only has a name.

    Returns:
      A ClassType. This ClassType will (temporarily) only have a name.
    """
    return pytd.ClassType(node.containing_type)


class ReplaceType(object):
  """Visitor for replacing types in a tree. Only changes BasicType nodes."""

  def __init__(self, mapping):
    self.mapping = mapping

  def VisitBasicType(self, node):
    if node.containing_type in self.mapping:
      return self.mapping[node.containing_type]
    else:
      return node


class InstantiateTemplates(object):
  """Tries to remove templates by instantiating the corresponding types.

  It will create classes that are named "base_type<element_type>", so e.g.
  a list of integers will literally be named "list<int>".

  Attributes:
    symbol_table: Symbol table for looking up templated classes.
  """

  def __init__(self, symbol_table):
    self.symbol_table = symbol_table
    self._instantiated_classes = {}

  def VisitTypeDeclUnit(self, node):
    """Adds the instantiated classes to the module. Removes templates."""
    old_classes = [c for c in node.classes if c.template is None]
    new_classes = self._instantiated_classes.values()
    return node._replace(classes=old_classes + new_classes)

  def _InstantiateClass(self, name, base_type, element_types):
    cls = self.symbol_table.Lookup(base_type.containing_type)
    names = [t.name for t in cls.template]
    mapping = {name: e for name, e in zip(names, element_types)}
    return cls._replace(name=name, template=None).Visit(ReplaceType(mapping))

  def VisitHomogeneousContainerType(self, node):
    """Converts a template type (container type) to a concrete class.

    This works by looking up the actual Class (using the lookup table passed
    when initializing the visitor) and substituting the parameters of the
    template everywhere in its definition. The new class is appended to the
    list of classes of this module. (Later on, the template we used is removed.)

    Args:
      node: An instance of HomogeneousContainerType

    Returns:
      A new BasicType pointing to an instantiation of the class.
    """
    base_type_name = node.base_type.Visit(PrintVisitor())
    element_type_name = node.element_type.Visit(PrintVisitor())
    name = "%s<%s>" % (base_type_name, element_type_name)
    if name not in self._instantiated_classes:
      self._instantiated_classes[name] = self._InstantiateClass(
          name, node.base_type, [node.element_type])
    return pytd.BasicType(name)

  def VisitGenericType(self, node):
    # TODO: implement this
    raise NotImplementedError()