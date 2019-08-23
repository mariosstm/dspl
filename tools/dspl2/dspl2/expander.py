# Copyright 2018 Google LLC
#
# Use of this source code is governed by a BSD-style
# license that can be found in the LICENSE file or at
# https://developers.google.com/open-source/licenses/bsd

from csv import DictReader
from urllib.parse import urlparse, urldefrag
from dspl2.jsonutil import (AsList, GetSchemaId, GetSchemaProp, GetUrl,
                            MakeIdKeyedDict)
from dspl2.rdfutil import (_DataFileFrame, FrameGraph, LoadGraph,
                           MakeSparqlSelectQuery)
import json
import rdflib


class Dspl2RdfExpander(object):
  """Expand CSV files in an DSPL2 RDF graph"""
  def __init__(self, getter):
    self.getter = getter
    self.graph = getter.graph
    self.subjects = set(self.graph.subjects())

  def _ExpandDimensionValue(self, dim, equivalentTypes, row_id, row):
    self.graph.add(
        (rdflib.URIRef(row_id),
         rdflib.RDF.type,
         rdflib.URIRef('http://schema.org/DimensionValue')))
    self.graph.add(
        (rdflib.URIRef(row_id),
         rdflib.URIRef('http://schema.org/dimension'),
         rdflib.URIRef(dim)))
    for type_id in equivalentTypes:
      self.graph.add(
          (rdflib.URIRef(row_id),
           rdflib.RDF.type,
           rdflib.URIRef(typeid)))
    for key, val in row.items():
      fields = key.split('@')
      if len(fields) > 1:
        # A language code is specified
        self.graph.add((rdflib.URIRef(row_id),
                        rdflib.URIRef('http://schema.org/' + fields[0]),
                        rdflib.Literal(val, language=fields[1])))
      else:
        self.graph.add((rdflib.URIRef(row_id),
                        rdflib.URIRef('http://schema.org/' + key),
                        rdflib.Literal(val)))

  def _ExpandCodeList(self, dim):
    # Set up the DimensionValue's triples.
    # Start with types
    if urlparse(dim).fragment:
      id_prefix = str(dim)
    else:
      id_prefix = str(dim) + '#' + str(self.graph.triples(
          subject=dim,
          predicate=rdflib.URIRef('http://schema.org/name'))[0])
    id_prefix += '='
    equivalentTypes = self.graph.objects(
        subject=dim,
        predicate=rdflib.URIRef('http://schema.org/equivalentType'))
    for codeList in self.graph.objects(
        subject=dim,
        predicate=rdflib.URIRef('http://schema.org/codeList')):
      if codeList not in self.subjects:
        self.graph.remove((dim, rdflib.URIRef('http://schema.org/codeList'), codeList))
        with self.getter.Fetch(str(codeList)) as f:
          reader = DictReader(f)
          for row in reader:
            self._ExpandDimensionValue(
                dim, equivalentTypes, id_prefix + row['codeValue'], row)

  def _ExpandFootnotes(self):
    for result in self.graph.query(
        MakeSparqlSelectQuery(
            ('?ds', 'a', 'schema:StatisticalDataset'),
            ('?ds', 'schema:footnote', '?fn'),
            ns_manager=self.graph.namespace_manager)):
      if result['fn'] not in self.subjects:
        self.graph.remove((result['ds'],
                           rdflib.URIRef('http://schema.org/footnote'),
                           result['fn']))
        id_prefix = urldefrag(str(result['ds'])).url
        with self.getter.Fetch(str(result['fn'])) as f:
          reader = DictReader(f)
          for row in reader:
            row_id = id_prefix + '#footnote=' + row['codeValue']
            self.graph.add(
                (rdflib.URIRef(row_id),
                 rdflib.RDF.type,
                 rdflib.URIRef('http://schema.org/StatisticalAnnotation')))
            for key, val in row.items():
              fields = key.split('@')
              if len(fields) > 1:
                # A language code is specified
                self.graph.add((rdflib.URIRef(row_id),
                                rdflib.URIRef('http://schema.org/' + fields[0]),
                                rdflib.Literal(val, language=fields[1])))
              else:
                self.graph.add((rdflib.URIRef(row_id),
                                rdflib.URIRef('http://schema.org/' + key),
                                rdflib.Literal(val)))

  def _GetDimensionDataForSlice(self, slice_id):
    ret = {}
    dims = sorted(
        self.graph.objects(
            subject=slice_id,
            predicate=rdflib.URIRef('http://schema.org/dimension')))
    for dim_id in dims:
      dim_type = list(self.graph.objects(
          subject=dim_id,
          predicate=rdflib.RDF.type))
      dim_equiv_types = list(self.graph.objects(
          subject=dim_id,
          predicate=rdflib.URIRef('http://schema.org/equivalentType')))
      csv_id = None
      for identifier in self.graph.objects(
          subject=dim_id,
          predicate=rdflib.URIRef('http://schema.org/identifier')):
        csv_id = identifier
        break
      if not csv_id:
        csv_id = urldefrag(dim_id).fragment
      if not csv_id:
        print("Unable to determine CSV ID for dimension", dim_id)
        exit(1)
      ret[csv_id] = {
          'id': dim_id,
          'type': dim_type,
          'types': dim_equiv_types
      }
    return ret

  def _GetMeasureDataForSlice(self, slice_id):
    ret = {}
    measures = sorted(
        self.graph.objects(
            subject=slice_id,
            predicate=rdflib.URIRef('http://schema.org/measure')))
    for measure_id in measures:
      unit_codes = list(self.graph.objects(
          subject=measure_id,
          predicate=rdflib.URIRef('http://schema.org/unitCode')))
      unit_texts = list(self.graph.objects(
          subject=measure_id,
          predicate=rdflib.URIRef('http://schema.org/unitText')))
      csv_id = None
      for identifier in self.graph.objects(
          subject=measure_id,
          predicate=rdflib.URIRef('http://schema.org/identifier')):
        csv_id = identifier
        break
      if not csv_id:
        csv_id = urldefrag(measure_id).fragment
      if not csv_id:
        print("Unable to determine CSV ID for metric", measure_id)
        exit(1)
      ret[csv_id] = {
          'id': measure_id,
          'unit_code': unit_codes,
          'unit_text': unit_texts,
      }
    return ret

  def _MakeSliceDataRowId(self, slice_id, dims, measures, row):
    ret = str(slice_id)
    if not urldefrag(slice_id).fragment:
      ret += '#'
    else:
      ret += '/'
    for dim in dims:
      ret += dim + '=' + row[dim]
      ret += '/'
    for measure in measures:
      ret += measure
      ret += '/'
    return ret

  def _ExpandObservationDimensionValue(self, dim, data, row_id, row):
    node_id = rdflib.BNode()
    self.graph.add((row_id, rdflib.URIRef('http://schema.org/dimensionValues'),
                    node_id))
    self.graph.add((node_id, rdflib.RDF.type,
                    rdflib.URIRef('http://schema.org/DimensionValue')))
    self.graph.add((node_id, rdflib.URIRef('http://schema.org/dimension'),
                    data['id']))
    for dim_type in data['type']:
      if dim_type.endswith('CategoricalDimension'):
        for type_id in data['types']:
          self.graph.add((node_id, rdflib.RDF.type, type_id))
        self.graph.add((node_id, rdflib.URIRef('http://schema.org/codeValue'),
                        rdflib.Literal(row[dim])))
      else:
        if data['types']:
          self.graph.add((node_id, rdflib.URIRef('http://schema.org/value'),
                          rdflib.Literal(
                              row[dim],
                              datatype=rdflib.URIRef(data['types'][0]))))
        else:
          self.graph.add((node_id, rdflib.URIRef('http://schema.org/value'),
                          rdflib.Literal(row[dim])))

  def _ExpandObservationMeasureValue(self, measure, data, row_id, row):
    node_id = rdflib.BNode()
    self.graph.add((row_id, rdflib.URIRef('http://schema.org/measureValues'),
                    node_id))
    self.graph.add((node_id, rdflib.RDF.type,
                    rdflib.URIRef('http://schema.org/MeasureValue')))
    for unit_code in data['unit_code']:
      self.graph.add((node_id, rdflib.URIRef('http://schema.org/unitCode'),
                      rdflib.Literal(unit_code)))
    for unit_text in data['unit_text']:
      self.graph.add((node_id, rdflib.URIRef('http://schema.org/unitCode'),
                      rdflib.Literal(unit_text)))
    self.graph.add((node_id, rdflib.URIRef('http://schema.org/value'),
                    rdflib.Literal(row[measure])))
    for footnote in row.get(measure + '*', '').split(';'):
      footnote_id = rdflib.BNode()
      self.graph.add((footnote_id, rdflib.RDF.type,
                      rdflib.URIRef('http://schema.org/StatisticalAnnotation')))
      self.graph.add((footnote_id, rdflib.URIRef('http://schema.org/codeValue'),
                      rdflib.Literal(footnote)))

  def _ExpandSliceData(self, slice_id):
    dim_data = self._GetDimensionDataForSlice(slice_id)
    measure_data = self._GetMeasureDataForSlice(slice_id)
    for data_id in self.graph.objects(
        subject=slice_id,
        predicate=rdflib.URIRef('http://schema.org/data')):
      if data_id not in self.subjects:
        with self.getter.Fetch(data_id) as f:
          reader = DictReader(f)
          for row in reader:
            row_id = rdflib.URIRef(self._MakeSliceDataRowId(
                slice_id, dim_data, measure_data, row))
            self.graph.add((slice_id, rdflib.URIRef('http://schema.org/data'), row_id))
            self.graph.add((row_id, rdflib.RDF.type,
                            rdflib.URIRef('http://schema.org/Observation')))
            self.graph.add((row_id,
                            rdflib.URIRef('http://schema.org/slice'),
                            slice_id))
            for dim, data in dim_data.items():
              self._ExpandObservationDimensionValue(dim, data, row_id, row)
            for measure, data in measure_data.items():
              self._ExpandObservationMeasureValue(measure, data, row_id, row)

  def Expand(self):
    for dim in set(self.graph.subjects(
        predicate=rdflib.RDF.type,
        object=rdflib.URIRef('http://schema.org/CategoricalDimension'))):
      self._ExpandCodeList(dim)
    self._ExpandFootnotes()
    for slice_id in set(self.graph.subjects(
        predicate=rdflib.RDF.type,
        object=rdflib.URIRef('http://schema.org/DataSlice'))):
      self._ExpandSliceData(slice_id)
    return self.graph


class Dspl2JsonLdExpander(object):
  def __init__(self, getter):
    self.getter = getter

  def _ExpandCodeList(self, dim):
    """Load a code list from CSV and return a list of JSON-LD objects."""
    codeList = []
    with self.getter.Fetch(GetSchemaProp(dim, 'codeList')) as f:
      reader = DictReader(f)
      for row in reader:
        if GetSchemaProp(dim, 'equivalentType'):
          row['@type'] = ['DimensionValue', GetSchemaProp(dim, 'equivalentType')]
        else:
          row['@type'] = 'DimensionValue'
        row['@id'] = GetSchemaId(dim) + '='
        row['@id'] += row['codeValue']
        row['dimension'] = GetSchemaId(dim)
        codeList.append(row)
    return codeList

  def _ExpandFootnotes(self, filename, json_val):
    """Load footnotes from CSV and return a list of JSON-LD objects."""
    footnotes = []
    with self.getter.Fetch(filename) as f:
      reader = DictReader(f)
      for row in reader:
        row['@type'] = 'StatisticalAnnotation'
        row['@id'] = GetSchemaId(json_val) + '#footnote='
        row['@id'] += row['codeValue']
        row['dataset'] = GetSchemaId(json_val)
        footnotes.append(row)
    return footnotes

  def _ExpandSliceData(self, slice, dim_defs_by_id):
    data = []
    with self.getter.Fetch(GetSchemaProp(slice, 'data')) as f:
      reader = DictReader(f)
      for row in reader:
        val = {}
        val['@type'] = 'Observation'
        val['slice'] = GetSchemaId(slice)
        val['dimensionValues'] = []
        val['measureValues'] = []
        for dim in AsList(GetSchemaProp(slice, 'dimension')):
          dim = GetUrl(dim)
          fragment = urlparse(dim).fragment
          dim_val = {
              '@type': 'DimensionValue',
              'dimension': dim,
          }
          dim_def = dim_defs_by_id.get(dim)
          if dim_def:
            if GetSchemaProp(dim_def, '@type') == 'CategoricalDimension':
              dim_val['codeValue'] = row[fragment]
            elif GetSchemaProp(dim_def, '@type') == 'TimeDimension':
              if GetSchemaProp(dim_def, 'equivalentType'):
                dim_val['value'] = {
                    '@type': GetSchemaProp(dim_def, 'equivalentType'),
                    '@value': row[fragment]
                }
              else:
                val['dimensionValues'][-1]['value'] = row[fragment]
          val['dimensionValues'].append(dim_val)

        for measure in AsList(GetSchemaProp(slice, 'measure')):
          measure = GetUrl(measure)
          fragment = urlparse(measure).fragment
          val['measureValues'].append({
              '@type': 'MeasureValue',
              'measure': measure,
              'value': row[fragment]
          })
          if row.get(fragment + '*'):
            val['measureValues'][-1]['footnote'] = [
                {
                    '@type': 'StatisticalAnnotation',
                    'codeValue': footnote
                }
                for footnote in row[fragment + '*'].split(';')
            ]
        data.append(val)
    return data

  def Expand(self):
    json_val = FrameGraph(self.getter.graph, frame=_DataFileFrame)
    for dim in AsList(GetSchemaProp(json_val, 'dimension')):
      if isinstance(dim.get('codeList'), str):
        dim['codeList'] = self._ExpandCodeList(dim)
    if isinstance(GetSchemaProp(json_val, 'footnote'), str):
      json_val['footnote'] = self._ExpandFootnotes(
          GetSchemaProp(json_val, 'footnote'), json_val)
    for slice in AsList(GetSchemaProp(json_val, 'slice')):
      dim_defs_by_id = MakeIdKeyedDict(
          AsList(GetSchemaProp(json_val, 'dimension')))
      if isinstance(GetSchemaProp(slice, 'data'), str):
        slice['data'] = self._ExpandSliceData(slice, dim_defs_by_id)
    return json_val