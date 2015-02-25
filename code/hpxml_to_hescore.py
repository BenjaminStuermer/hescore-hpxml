'''
Created on Mar 4, 2014

@author: nmerket
'''
# Python standard library imports
import os
import sys
import argparse
import datetime as dt
import logging
import re
import json
import math
from lxml import etree

try:
    from collections import OrderedDict
except ImportError:
    OrderedDict = dict

logging.basicConfig(level=logging.ERROR, format='%(levelname)s:%(message)s')

# My imports
thisdir = os.path.dirname(os.path.abspath(__file__))
nsre = re.compile(r'([a-zA-Z][a-zA-Z0-9]*):')


def tobool(x):
    if x is None:
        return None
    elif x.lower() == 'true':
        return True
    else:
        assert x.lower() == 'false'
        return False


def convert_to_type(type_, value):
    if value is None:
        return value
    else:
        return type_(value)


# Base class for errors in this module
class HPXMLtoHEScoreError(Exception):
    pass


class TranslationError(HPXMLtoHEScoreError):
    pass


class InputOutOfBounds(HPXMLtoHEScoreError):
    def __init__(self, inpname, value):
        self.inpname = inpname
        self.value = value

    @property
    def message(self):
        return '{} is out of bounds: {}'.format(self.inpname, self.value)

    def __str__(self):
        return self.message


def unspin_azimuth(azimuth):
    while azimuth >= 360:
        azimuth -= 360
    while azimuth < 0:
        azimuth += 360
    return azimuth


def round_to_nearest(x, vals):
    return min(vals, key=lambda y: abs(x - y))


class HPXMLtoHEScoreTranslator(object):
    schemaversions = ('hpxml-2.1.0', 'hpxml-1.1.1')

    def __init__(self, hpxmlfilename):

        # Parse the document and detect the version
        self.hpxmldoc = etree.parse(hpxmlfilename)
        for sv in self.schemaversions:
            self.schemapath = os.path.join(thisdir, 'schemas', sv, 'HPXML.xsd')
            schematree = etree.parse(self.schemapath)
            self.schema = etree.XMLSchema(schematree)
            if self.schema.validate(self.hpxmldoc):
                break
            else:
                self.schemapath = None
                self.schema = None
        if self.schema is None:
            raise TranslationError(
                '{} failed to validate against all the following HPXML schemas: {}'.format(hpxmlfilename, ', '.join(
                    self.schemaversions)))
        self.ns = {'xs': 'http://www.w3.org/2001/XMLSchema'}
        self.ns['h'] = schematree.xpath('//xs:schema/@targetNamespace', namespaces=self.ns)[0]

    def xpath(self, el, xpathquery, aslist=False, **kwargs):
        res = el.xpath(xpathquery, namespaces=self.ns, **kwargs)
        if aslist:
            return res
        if isinstance(res, list):
            if len(res) == 0:
                return None
            elif len(res) == 1:
                return res[0]
            else:
                return res
        else:
            return res

    def get_wall_assembly_code(self, hpxmlwall):
        xpath = self.xpath
        ns = self.ns
        wallid = xpath(hpxmlwall, 'h:SystemIdentifier/@id')

        # siding
        sidingmap = {'wood siding': 'wo',
                     'stucco': 'st',
                     'synthetic stucco': 'st',
                     'vinyl siding': 'vi',
                     'aluminum siding': 'al',
                     'brick veneer': 'br',
                     'asbestos siding': 'wo',
                     'fiber cement siding': 'wo',
                     'composite shingle siding': 'wo',
                     'masonite siding': 'wo',
                     'other': None}

        # construction type
        wall_type = xpath(hpxmlwall, 'name(h:WallType/*)')
        if wall_type == 'WoodStud':
            has_rigid_ins = False
            cavity_rvalue = 0
            for lyr in hpxmlwall.xpath('h:Insulation/h:Layer', namespaces=ns):
                installation_type = xpath(lyr, 'h:InstallationType/text()')
                if xpath(lyr, 'h:InsulationMaterial/h:Rigid') is not None and \
                                installation_type == 'continuous':
                    has_rigid_ins = True
                else:
                    cavity_rvalue += float(xpath(lyr, 'h:NominalRValue/text()'))
            if tobool(xpath(hpxmlwall, 'h:WallType/h:WoodStud/h:ExpandedPolystyreneSheathing/text()')) or has_rigid_ins:
                wallconstype = 'ps'
                rvalue = round_to_nearest(cavity_rvalue, (0, 3, 7, 11, 13, 15, 19, 21))
            elif tobool(xpath(hpxmlwall, 'h:WallType/h:WoodStud/h:OptimumValueEngineering/text()')):
                wallconstype = 'ov'
                rvalue = round_to_nearest(cavity_rvalue, (19, 21, 27, 33, 38))
            else:
                wallconstype = 'wf'
                rvalue = round_to_nearest(cavity_rvalue, (0, 3, 7, 11, 13, 15, 19, 21))
            hpxmlsiding = xpath(hpxmlwall, 'h:Siding/text()')
            try:
                sidingtype = sidingmap[hpxmlsiding]
            except KeyError:
                raise TranslationError('Wall %s: Exterior finish information is missing' % wallid)
            else:
                if sidingtype is None:
                    raise TranslationError(
                        'Wall %s: There is no HEScore wall siding equivalent for the HPXML option: %s' %
                        (wallid, hpxmlsiding))
        elif wall_type == 'StructuralBrick':
            wallconstype = 'br'
            sidingtype = 'nn'
            rvalue = 0
            for lyr in hpxmlwall.xpath('h:Insulation/h:Layer', namespaces=ns):
                rvalue += float(xpath(lyr, 'h:NominalRValue/text()'))
            rvalue = round_to_nearest(rvalue, (0, 5, 10))
        elif wall_type in ('ConcreteMasonryUnit', 'Stone'):
            wallconstype = 'cb'
            rvalue = 0
            for lyr in hpxmlwall.xpath('h:Insulation/h:Layer', namespaces=ns):
                rvalue += float(xpath(lyr, 'h:NominalRValue/text()'))
            rvalue = round_to_nearest(rvalue, (0, 3, 6))
            hpxmlsiding = xpath(hpxmlwall, 'h:Siding/text()')
            if hpxmlsiding is None:
                sidingtype = 'nn'
            else:
                sidingtype = sidingmap[hpxmlsiding]
                if sidingtype not in ('st', 'br'):
                    raise TranslationError(
                        'Wall %s: is a CMU and needs a siding of stucco, brick, or none to translate to HEScore. It has a siding type of %s' % (
                            wallid, hpxmlsiding))
        elif wall_type == 'StrawBale':
            wallconstype = 'sb'
            rvalue = 0
            sidingtype = 'st'
        else:
            raise TranslationError('Wall type %s not supported' % wall_type)

        return 'ew%s%02d%s' % (wallconstype, rvalue, sidingtype)

    def get_window_code(self, window):
        xpath = self.xpath
        ns = self.ns

        window_code = None
        frame_type = xpath(window, 'name(h:FrameType/*)')
        glass_layers = xpath(window, 'h:GlassLayers/text()')
        glass_type = xpath(window, 'h:GlassType/text()')
        gas_fill = xpath(window, 'h:GasFill/text()')
        if frame_type in ('Aluminum', 'Metal'):
            thermal_break = tobool(xpath(window, 'h:FrameType/*/h:ThermalBreak/text()'))
            if thermal_break:
                # Aluminum with Thermal Break
                if glass_layers in ('double-pane', 'single-paned with storms', 'single-paned with low-e storms'):
                    if glass_layers == 'double-pane' and glass_type == 'low-e' and gas_fill == 'argon':
                        window_code = 'dpeaab'
                    elif glass_type is not None and glass_type == 'reflective':
                        # TODO: figure out if 'reflective' is close enough to 'solar-control' low-e
                        window_code = 'dseab'
                    elif glass_type is not None and glass_type.startswith('tinted'):
                        window_code = 'dtab'
                    else:
                        window_code = 'dcab'
            else:
                # Aluminum
                if glass_layers == 'single-pane':
                    if glass_type is not None and glass_type in ('tinted', 'low-e', 'tinted/reflective'):
                        window_code = 'stna'
                    else:
                        window_code = 'scna'
                elif glass_layers in ('double-pane', 'single-paned with storms', 'single-paned with low-e storms'):
                    if glass_type is not None and glass_type in ('reflective', 'tinted/reflective'):
                        window_code = 'dseaa'
                    elif glass_type is not None and glass_type == 'tinted':
                        window_code = 'dtaa'
                    else:
                        window_code = 'dcaa'
        elif frame_type in ('Vinyl', 'Wood', 'Fiberglass', 'Composite'):
            # Wood or Vinyl
            if glass_layers == 'single-pane':
                if glass_type is not None and glass_type in ('tinted', 'low-e', 'tinted/reflective'):
                    window_code = 'stnw'
                else:
                    window_code = 'scnw'
            elif glass_layers in ('double-pane', 'single-paned with storms', 'single-paned with low-e storms'):
                if (glass_layers == 'double-pane' and glass_type == 'low-e') or \
                                glass_layers == 'single-paned with low-e storms':
                    if gas_fill == 'argon' and glass_layers == 'double-pane':
                        window_code = 'dpeaaw'
                    else:
                        window_code = 'dpeaw'
                elif glass_type == 'reflective':
                    # TODO: figure out if 'reflective' is close enough to 'solar-control' low-e
                    if gas_fill == 'argon' and glass_layers == 'double-pane':
                        window_code = 'dseaaw'
                    else:
                        window_code = 'dseaw'
                elif glass_type is not None and glass_type.startswith('tinted'):
                    window_code = 'dtaw'
                else:
                    window_code = 'dcaw'
            elif glass_layers == 'triple-pane':
                window_code = 'thmabw'

        if window_code is None:
            raise TranslationError('Cannot translate window type.')
        return window_code

    heat_pump_type_map = {'water-to-air': 'gchp',
                          'water-to-water': 'gchp',
                          'air-to-air': 'heat_pump',
                          'mini-split': 'heat_pump',
                          'ground-to-air': 'gchp'}

    def get_heating_system_type(self, htgsys):
        xpath = self.xpath
        ns = self.ns

        sys_heating = OrderedDict()
        if htgsys.tag.endswith('HeatPump'):
            sys_heating['fuel_primary'] = 'electric'
            heat_pump_type = xpath(htgsys, 'h:HeatPumpType/text()')
            if heat_pump_type is None:
                sys_heating['type'] = 'heat_pump'
            else:
                sys_heating['type'] = self.heat_pump_type_map[heat_pump_type]
        else:
            assert htgsys.tag.endswith('HeatingSystem')
            sys_heating['fuel_primary'] = self.fuel_type_mapping[xpath(htgsys, 'h:HeatingSystemFuel/text()')]
            hpxml_heating_type = xpath(htgsys, 'name(h:HeatingSystemType/*)')
            try:
                sys_heating['type'] = {'Furnace': 'central_furnace',
                                       'WallFurnace': 'wall_furnace',
                                       'Boiler': 'boiler',
                                       'ElectricResistance': 'baseboard'}[hpxml_heating_type]
            except KeyError:
                raise TranslationError('HEScore does not support the HPXML HeatingSystemType %s' % hpxml_heating_type)

        if not (sys_heating['type'] in ('furnace', 'baseboard') and sys_heating['fuel_primary'] == 'electric'):
            eff_units = {'heat_pump': 'HSPF',
                         'central_furnace': 'AFUE',
                         'wall_furnace': 'AFUE',
                         'boiler': 'AFUE',
                         'gchp': 'COP'}[sys_heating['type']]
            getefficiencyxpathexpr = '(h:AnnualHeatingEfficiency|h:AnnualHeatEfficiency)[h:Units=$effunits]/h:Value/text()'
            eff_els = htgsys.xpath(getefficiencyxpathexpr, namespaces=ns,
                                   effunits=eff_units)
            if len(eff_els) == 0:
                # Use the year instead
                sys_heating['efficiency_method'] = 'shipment_weighted'
                sys_heating['year'] = int(htgsys.xpath('(h:YearInstalled|h:ModelYear)/text()', namespaces=ns)[0])
            else:
                # Use the efficiency of the first element found.
                sys_heating['efficiency_method'] = 'user'
                sys_heating['efficiency'] = float(eff_els[0])
        else:
            sys_heating['efficiency_method'] = None
        sys_heating['capacity'] = convert_to_type(float, xpath(htgsys, 'h:HeatingCapacity/text()'))
        return sys_heating

    def get_cooling_system_type(self, clgsys):
        xpath = self.xpath
        ns = self.ns

        sys_cooling = OrderedDict()
        if clgsys.tag.endswith('HeatPump'):
            heat_pump_type = xpath(clgsys, 'h:HeatPumpType/text()')
            if heat_pump_type is None:
                sys_cooling['type'] = 'heat_pump'
            else:
                sys_cooling['type'] = self.heat_pump_type_map[heat_pump_type]
        else:
            assert clgsys.tag.endswith('CoolingSystem')
            hpxml_cooling_type = xpath(clgsys, 'h:CoolingSystemType/text()')
            sys_cooling['type'] = {'central air conditioning': 'split_dx',
                                   'room air conditioner': 'packaged_dx',
                                   'mini-split': 'split_dx'}[hpxml_cooling_type]
        # cooling efficiency
        eff_units = {'split_dx': 'SEER',
                     'packaged_dx': 'EER',
                     'heat_pump': 'SEER',
                     'gchp': 'EER',
                     'dec': None,
                     'iec': None,
                     'idec': None}[sys_cooling['type']]
        if eff_units is not None:
            clgeffxpathexpr = '(h:AnnualCoolingEfficiency|h:AnnualCoolEfficiency)[h:Units=$effunits]/h:Value/text()'
            eff_els = clgsys.xpath(clgeffxpathexpr, namespaces=ns,
                                   effunits=eff_units)
        else:
            eff_els = []
        if len(eff_els) == 0:
            # Use the year instead
            sys_cooling['efficiency_method'] = 'shipment_weighted'
            sys_cooling['year'] = int(clgsys.xpath('(h:YearInstalled|h:ModelYear)/text()', namespaces=ns)[0])
        else:
            # Use the efficiency of the first element found.
            sys_cooling['efficiency_method'] = 'user'
            sys_cooling['efficiency'] = float(eff_els[0])
        sys_cooling['capacity'] = convert_to_type(float, xpath(clgsys, 'h:CoolingCapacity/text()'))
        return sys_cooling

    def get_or_create_child(self, parent, childname, insertpos=-1):
        child = parent.find(childname)
        if child is None:
            child = etree.Element(childname)
            parent.insert(insertpos, child)
        return child

    def addns(self, x):
        repl = lambda m: ('{%(' + m.group(1) + ')s}') % self.ns
        return nsre.sub(repl, x)

    def insert_element_in_order(self, parent, child, elorder):
        fullelorder = map(self.addns, elorder)
        childidx = fullelorder.index(child.tag)
        if len(parent) == 0:
            parent.append(child)
        else:
            for i, el in enumerate(parent):
                try:
                    idx = fullelorder.index(el.tag)
                except ValueError:
                    continue
                if idx > childidx:
                    parent.insert(i, child)
                    return

    def apply_nrel_assumptions(self, b):
        xpath = self.xpath
        addns = self.addns
        ns = self.ns

        # Get some element ordering from the schemas that we might need later.
        hpxml_base_elements = etree.parse(os.path.join(os.path.dirname(self.schemapath), 'BaseElements.xsd'))
        site_element_order = hpxml_base_elements.xpath(
            '//xs:element[@name="Site"][ancestor::xs:complexType[@name="BuildingDetailsType"]]/xs:complexType/xs:sequence/xs:element/@name',
            namespaces=ns)
        site_element_order = ['h:' + x for x in site_element_order]
        wall_element_order = hpxml_base_elements.xpath('//xs:element[@name="Siding"]/parent::node()/xs:element/@name',
                                                       namespaces=ns)
        wall_element_order = ['h:' + x for x in wall_element_order]

        # Assume the back of the house has the largest window area
        site = self.get_or_create_child(xpath(b, 'h:BuildingDetails/h:BuildingSummary'), addns('h:Site'), 0)
        xpath(site, 'h:OrientationOfFrontOfHome/text()')
        if xpath(site, 'h:AzimuthOfFrontOfHome/text()') is None and \
                        xpath(site, 'h:OrientationOfFrontOfHome/text()') is None:
            window_areas = {}
            for window in xpath(b, 'h:BuildingDetails/h:Enclosure/h:Windows/h:Window'):
                azimuth = self.get_nearest_azimuth(xpath(window, 'h:Azimuth/text()'),
                                                   xpath(window, 'h:Orientation/text()'))
                window_area = float(xpath(window, 'h:Area/text()'))
                try:
                    window_areas[azimuth] += window_area
                except KeyError:
                    window_areas[azimuth] = window_area
            back_azimuth = max(window_areas.items(), key=lambda x: x[1])[0]
            front_azimuth = (back_azimuth + 180) % 360
            azimuth_el = etree.Element(addns('h:AzimuthOfFrontOfHome'))
            azimuth_el.text = str(front_azimuth)
            self.insert_element_in_order(site, azimuth_el, site_element_order)
            logging.debug('Assuming the house faces %d', front_azimuth)

        # Assume stucco if none specified
        if xpath(b, 'h:BuildingDetails/h:Enclosure/h:Walls/h:Wall/h:Siding') is None:
            logging.debug('Assuming stucco siding')
            for wall in b.xpath('h:BuildingDetails/h:Enclosure/h:Walls/h:Wall', namespaces=ns):
                siding_el = etree.Element(addns('h:Siding'))
                siding_el.text = 'stucco'
                self.insert_element_in_order(wall, siding_el, wall_element_order)

    hpxml_orientation_to_azimuth = {'north': 0,
                                    'northeast': 45,
                                    'east': 90,
                                    'southeast': 135,
                                    'south': 180,
                                    'southwest': 225,
                                    'west': 270,
                                    'northwest': 315}

    fuel_type_mapping = {'electricity': 'electric',
                         'renewable electricity': 'electric',
                         'natural gas': 'natural_gas',
                         'renewable natural gas': 'natural_gas',
                         'fuel oil': 'fuel_oil',
                         'fuel oil 1': 'fuel_oil',
                         'fuel oil 2': 'fuel_oil',
                         'fuel oil 4': 'fuel_oil',
                         'fuel oil 5/6': 'fuel_oil',
                         'propane': 'lpg'}

    def get_nearest_azimuth(self, azimuth=None, orientation=None):
        if azimuth is not None:
            return int(round(float(azimuth) / 45.)) % 8 * 45
        else:
            if orientation is None:
                raise TranslationError('Either an orientation or azimuth is required.')
            return self.hpxml_orientation_to_azimuth[orientation]

    def hpxml_to_hescore_json(self, outfile, *args, **kwargs):
        hescore_bldg = self.hpxml_to_hescore_dict(*args, **kwargs)
        json.dump(hescore_bldg, outfile, indent=2)

    def hpxml_to_hescore_dict(self, hpxml_bldg_id=None, nrel_assumptions=False):
        '''
        Convert a HPXML building file to a python dict with the same structure as the HEScore API
        
        hpxml_bldg_id (optional) - If there is more than one <Building> element in an HPXML file,
            use this one. Otherwise just use the first one.
        nrel_assumptions - Apply the NREL assumptions for files that don't explicitly have certain fields.
        '''
        xpath = self.xpath
        ns = self.ns

        # Load the xml document into lxml etree
        if hpxml_bldg_id is not None:
            b = xpath(self.hpxmldoc, 'h:Building[h:BuildingID/@id=$bldgid]', bldgid=hpxml_bldg_id)
        else:
            b = xpath(self.hpxmldoc, 'h:Building[1]')

        # Apply NREL assumptions, if requested
        if nrel_assumptions:
            self.apply_nrel_assumptions(b)
        self.schema.assertValid(self.hpxmldoc)

        # Create return dict
        hescore_inputs = OrderedDict()
        hescore_inputs['building_address'] = self._get_building_address(b)
        bldg = OrderedDict()
        hescore_inputs['building'] = bldg
        bldg['about'] = self._get_building_about(b)
        bldg['zone'] = OrderedDict()
        bldg['zone']['zone_roof'] = None # to save the spot in the order
        bldg['zone']['zone_floor'] = self._get_building_zone_floor(b)
        footprint_area = self._get_footprint_area(bldg)
        bldg['zone']['zone_roof'] = self._get_building_zone_roof(b, footprint_area)
        bldg['zone']['zone_roof'][0]['zone_skylight'] = self._get_skylights(b)
        for zone_roof in bldg['zone']['zone_roof'][1:]:
            zone_roof['zone_skylight'] = {'skylight_area': 0}
        bldg['zone']['wall_construction_same'] = False
        bldg['zone']['window_construction_same'] = False
        bldg['zone']['zone_wall'] = self._get_building_zone_wall(b, bldg['about'])
        bldg['systems'] = OrderedDict()
        bldg['systems']['heating'] = self._get_systems_heating(b)
        bldg['systems']['cooling'] = self._get_systems_cooling(b)
        if not (bldg['systems']['cooling']['type'] == 'none' and bldg['systems']['heating']['type'] == 'none'):
            bldg['systems']['hvac_distribution'] = self._get_systems_hvac_distribution(b)
        bldg['systems']['domestic_hot_water'] = self._get_systems_dhw(b, bldg['systems']['heating'])
        self._remove_hidden_keys(hescore_inputs)

        # Validate
        self._validate_hescore_inputs(hescore_inputs)

        return hescore_inputs

    @staticmethod
    def _get_footprint_area(bldg):
        floor_area = bldg['about']['conditioned_floor_area']
        stories = bldg['about']['num_floor_above_grade']
        if bldg['zone']['zone_floor']['foundation_type'] == 'cond_basement':
            stories += 1
        return int(floor_area / stories)

    @staticmethod
    def _remove_hidden_keys(d):
        if isinstance(d, dict):
            for key, value in d.items():
                if key.startswith('_'):
                    del d[key]
                    continue
                HPXMLtoHEScoreTranslator._remove_hidden_keys(value)
        elif isinstance(d, (list, tuple)):
            for item in d:
                HPXMLtoHEScoreTranslator._remove_hidden_keys(item)

    def _get_building_address(self,b):
        xpath = self.xpath
        ns = self.ns
        bldgaddr = OrderedDict()
        hpxmladdress = xpath(b, 'h:Site/h:Address[h:AddressType="street"]')
        if hpxmladdress is None:
            raise TranslationError('The house address must be a street address.')
        bldgaddr['address'] = ' '.join(hpxmladdress.xpath('h:Address1/text() | h:Address2/text()', namespaces=ns))
        bldgaddr['city'] = xpath(b, 'h:Site/h:Address/h:CityMunicipality/text()')
        bldgaddr['state'] = xpath(b, 'h:Site/h:Address/h:StateCode/text()')
        bldgaddr['zip_code'] = xpath(b, 'h:Site/h:Address/h:ZipCode/text()')
        transaction_type = xpath(self.hpxmldoc, 'h:XMLTransactionHeaderInformation/h:Transaction/text()')
        if transaction_type == 'create':
            bldgaddr['assessment_type'] = {'audit': 'initial',
                                           'proposed workscope': 'alternative',
                                           'approved workscope': 'alternative',
                                           'construction-period testing/daily test out': 'test',
                                           'job completion testing/final inspection': 'final',
                                           'quality assurance/monitoring': 'qa'}[
                xpath(b, 'h:ProjectStatus/h:EventType/text()')]
        else:
            assert transaction_type == 'update'
            bldgaddr['assessment_type'] = 'corrected'
        return bldgaddr

    def _get_building_about(self,b):
        xpath = self.xpath
        ns = self.ns
        bldg_about = OrderedDict()
        projstatdateel = b.find('h:ProjectStatus/h:Date', namespaces=ns)
        if projstatdateel is None:
            bldg_about['assessment_date'] = dt.date.today()
        else:
            bldg_about['assessment_date'] = dt.datetime.strptime(projstatdateel.text, '%Y-%m-%d').date()
        bldg_about['assessment_date'] = bldg_about['assessment_date'].isoformat()

        # TODO: See if we can map more of these facility types
        residential_facility_type = xpath(b,
                                          'h:BuildingDetails/h:BuildingSummary/h:BuildingConstruction/h:ResidentialFacilityType/text()')
        try:
            bldg_about['shape'] = {'single-family detached': 'rectangle',
                                   'single-family attached': 'town_house',
                                   'manufactured home': None,
                                   '2-4 unit building': None,
                                   '5+ unit building': None,
                                   'multi-family - uncategorized': None,
                                   'multi-family - town homes': 'town_house',
                                   'multi-family - condos': None,
                                   'apartment unit': None,
                                   'studio unit': None,
                                   'other': None,
                                   'unknown': None
            }[residential_facility_type]
        except KeyError:
            raise TranslationError('ResidentialFacilityType is required in the HPXML document')
        if bldg_about['shape'] is None:
            raise TranslationError(
                'Cannot translate HPXML ResidentialFacilityType of %s into HEScore building shape' % residential_facility_type)
        if bldg_about['shape'] == 'town_house':
            # TODO: what to do with a house that is attached on three sides?
            # TODO: pull this info from the geometry
            hpxml_surroundings = xpath(b, 'h:BuildingDetails/h:BuildingSummary/h:Site/h:Surroundings/text()')
            try:
                bldg_about['town_house_walls'] = {'stand-alone': None,
                                                  'attached on one side': 'back_right_front',
                                                  'attached on two sides': 'back_front',
                                                  'attached on three sides': None
                }[hpxml_surroundings]
            except KeyError:
                raise TranslationError('Site/Surroundings element is required in the HPXML document for town houses')
            if bldg_about['town_house_walls'] is None:
                raise TranslationError(
                    'Cannot translate HPXML Site/Surroundings element value of %s into HEScore town_house_walls' % hpxml_surroundings)

        bldg_cons_el = b.find('h:BuildingDetails/h:BuildingSummary/h:BuildingConstruction', namespaces=ns)
        bldg_about['year_built'] = int(xpath(bldg_cons_el, 'h:YearBuilt/text()'))
        nbedrooms = int(xpath(bldg_cons_el, 'h:NumberofBedrooms/text()'))
        if nbedrooms > 10:
            nbedrooms = 10
        bldg_about['number_bedrooms'] = nbedrooms
        bldg_about['num_floor_above_grade'] = int(
            math.ceil(float(xpath(bldg_cons_el, 'h:NumberofConditionedFloorsAboveGrade/text()'))))
        avg_ceiling_ht = xpath(bldg_cons_el, 'h:AverageCeilingHeight/text()')
        if avg_ceiling_ht is None:
            avg_ceiling_ht = float(xpath(bldg_cons_el, 'h:ConditionedBuildingVolume/text()')) / \
                             float(xpath(bldg_cons_el, 'h:ConditionedFloorArea/text()'))
        else:
            avg_ceiling_ht = float(avg_ceiling_ht)
        bldg_about['floor_to_ceiling_height'] = int(round(avg_ceiling_ht))
        bldg_about['conditioned_floor_area'] = int(round(float(
            xpath(b, 'h:BuildingDetails/h:BuildingSummary/h:BuildingConstruction/h:ConditionedFloorArea/text()'))))

        site_el = xpath(b, 'h:BuildingDetails/h:BuildingSummary/h:Site')
        house_azimuth = self.get_nearest_azimuth(xpath(site_el, 'h:AzimuthOfFrontOfHome/text()'),
                                                 xpath(site_el, 'h:OrientationOfFrontOfHome/text()'))
        bldg_about['orientation'] = {0: 'north',
                                     45: 'north_east',
                                     90: 'east',
                                     135: 'south_east',
                                     180: 'south',
                                     225: 'south_west',
                                     270: 'west',
                                     315: 'north_west'}[house_azimuth]
        self.sidemap = {house_azimuth: 'front', (house_azimuth + 90) % 360: 'right',
                        (house_azimuth + 180) % 360: 'back', (house_azimuth + 270) % 360: 'left'}

        blower_door_test = None
        air_infilt_est = None
        for air_infilt_meas in b.xpath('h:BuildingDetails/h:Enclosure/h:AirInfiltration/h:AirInfiltrationMeasurement',
                                       namespaces=ns):
            # Take the last blower door test that is in CFM50, or if that's not available, ACH50
            if xpath(air_infilt_meas, 'h:TypeOfInfiltrationMeasurement/text()') == 'blower door':
                house_pressure = convert_to_type(int, xpath(air_infilt_meas, 'h:HousePressure/text()'))
                blower_door_test_units = xpath(air_infilt_meas, 'h:BuildingAirLeakage/h:UnitofMeasure/text()')
                if house_pressure == 50 and (blower_door_test_units == 'CFM' or
                                                 (blower_door_test_units == 'ACH' and blower_door_test is None)):
                    blower_door_test = air_infilt_meas
            elif xpath(air_infilt_meas, 'h:TypeOfInfiltrationMeasurement/text()') == 'estimate':
                air_infilt_est = air_infilt_meas
        if blower_door_test is not None:
            bldg_about['blower_door_test'] = True
            if xpath(blower_door_test, 'h:BuildingAirLeakage/h:UnitofMeasure/text()') == 'CFM':
                bldg_about['envelope_leakage'] = float(
                    xpath(blower_door_test, 'h:BuildingAirLeakage/h:AirLeakage/text()'))
            else:
                assert xpath(blower_door_test, 'h:BuildingAirLeakage/h:UnitofMeasure/text()') == 'ACH'
                bldg_about['envelope_leakage'] = bldg_about['floor_to_ceiling_height'] * bldg_about[
                    'conditioned_floor_area'] * \
                                                 float(xpath(blower_door_test,
                                                             'h:BuildingAirLeakage/h:AirLeakage/text()')) / 60.
                bldg_about['envelope_leakage'] = int(round(bldg_about['envelope_leakage']))
        else:
            bldg_about['blower_door_test'] = False
            if b.xpath('count(h:BuildingDetails/h:Enclosure/h:AirInfiltration/h:AirSealing)', namespaces=ns) > 0 or \
                    (air_infilt_est is not None and
                             xpath(air_infilt_est, 'h:LeakinessDescription/text()') in ('tight', 'very tight')):
                bldg_about['air_sealing_present'] = True
            else:
                bldg_about['air_sealing_present'] = False
        return bldg_about

    def _get_building_zone_roof(self, b, footprint_area):
        ns = self.ns
        xpath = self.xpath

        # building.zone.zone_roof--------------------------------------------------
        attics = xpath(b, '//h:Attic', aslist=True)
        roofs = xpath(b, '//h:Roof', aslist=True)
        rooftypemap = {'cape cod': 'cath_ceiling',
                       'cathedral ceiling': 'cath_ceiling',
                       'flat roof': 'cath_ceiling',
                       'unvented attic': 'vented_attic',
                       'vented attic': 'vented_attic',
                       'venting unknown attic': 'vented_attic',
                       'other': None}
        attic_floor_rvalues = (0, 3, 6, 9, 11, 19, 21, 25, 30, 38, 44, 49, 60)
        roof_center_of_cavity_rvalues = \
            {'wf': {'co': dict(zip((0, 11, 13, 15, 19, 21), (2.7, 13.6, 15.6, 17.6, 21.6, 23.6))),
                    'wo': dict(zip((0, 11, 13, 15, 19, 21, 27), (3.2, 14.1, 16.1, 18.1, 22.1, 24.1, 30.1))),
                    'rc': dict(zip((0, 11, 13, 15, 19, 21, 27), (2.2, 13.2, 15.2, 17.2, 21.2, 23.2, 29.2))),
                    'lc': dict(zip((0, 11, 13, 15, 19, 21, 27), (2.3, 13.2, 15.2, 17.2, 21.2, 23.2, 29.2))),
                    'tg': dict(zip((0, 11, 13, 15, 19, 21, 27), (2.3, 13.2, 15.2, 17.2, 21.2, 23.2, 29.2)))},
             'rb': {'co': {0: 5},
                    'wo': {0: 5.5},
                    'rc': {0: 4.5},
                    'lc': {0: 4.6},
                    'tg': {0: 4.6}},
             'ps': {'co': dict(zip((0, 11, 13, 15), (6.8, 17.8, 19.8, 21.8))),
                    'wo': dict(zip((0, 11, 13, 15, 19, 21), (7.3, 18.3, 20.3, 22.3, 26.3, 28.3))),
                    'rc': dict(zip((0, 11, 13, 15, 19, 21), (6.4, 17.4, 19.4, 21.4, 25.4, 27.4))),
                    'lc': dict(zip((0, 11, 13, 15, 19, 21), (6.4, 17.4, 19.4, 21.4, 25.4, 27.4))),
                    'tg': dict(zip((0, 11, 13, 15, 19, 21), (6.4, 17.4, 19.4, 21.4, 25.4, 27.4)))}}

        atticds = []
        for attic in attics:
            atticd = {}
            atticds.append(atticd)
            atticid = xpath(attic, 'h:SystemIdentifier/@id')
            roof = xpath(b, '//h:Roof[h:SystemIdentifier/@id=$roofid]', roofid=xpath(attic, 'h:AttachedToRoof/@idref'))
            if roof is None:
                if len(roofs) == 1:
                    roof = roofs[0]
                else:
                    raise TranslationError('Attic {} does not have a roof associated with it.'.format(xpath(attic, 'h:SystemIdentifier/@id')))

            # Roof id to use to match skylights later
            atticd['_roofid'] = xpath(roof, 'h:SystemIdentifier/@id')

            # Roof area
            atticd['roof_area'] = convert_to_type(float, xpath(attic, 'h:Area/text()'))
            if atticd['roof_area'] is None:
                if len(attics) == 1 and len(roofs) == 1:
                    atticd['roof_area'] = footprint_area
                else:
                    raise TranslationError('If there are more than one Attic elements, each needs an area.')

            # Roof type
            hpxml_attic_type = xpath(attic, 'h:AtticType/text()')
            atticd['rooftype'] = rooftypemap[hpxml_attic_type]
            if atticd['rooftype'] is None:
                raise TranslationError(
                    'Attic {}: Cannot translate HPXML AtticType {} to HEScore rooftype.'.format(atticid,
                                                                                                hpxml_attic_type))

            # Roof color
            try:
                atticd['roofcolor'] = {'light': 'light', 'medium': 'medium', 'dark': 'dark', 'reflective': 'white'}[
                    xpath(roof, 'h:RoofColor/text()')]
            except KeyError:
                raise TranslationError('Attic {}: Invalid or missing RoofColor'.format(atticid))

            # Exterior finish
            hpxml_roof_type = xpath(roof, 'h:RoofType/text()')
            try:
                atticd['extfinish'] = {'shingles': 'co',
                                       'slate or tile shingles': 'lc',
                                       'wood shingles or shakes': 'wo',
                                       'asphalt or fiberglass shingles': 'co',
                                       'metal surfacing': 'co',
                                       'expanded polystyrene sheathing': None,
                                       'plastic/rubber/synthetic sheeting': 'tg',
                                       'concrete': 'lc',
                                       'cool roof': None,
                                       'green roof': None,
                                       'no one major type': None,
                                       'other': None}[hpxml_roof_type]
                assert atticd['extfinish'] is not None
            except (KeyError, AssertionError):
                raise TranslationError(
                    'Attic {}: HEScore does not have an analogy to the HPXML roof type: {}'.format(atticid,
                                                                                                   hpxml_roof_type))

            # construction type
            has_rigid_sheathing = xpath(attic,
                                        'boolean(h:AtticRoofInsulation/h:Layer[h:NominalRValue > 0][h:InstallationType="continuous"][boolean(h:InsulationMaterial/h:Rigid)])')
            has_radiant_barrier = xpath(roof, 'h:RadiantBarrier="true"')
            if has_radiant_barrier:
                atticd['roofconstype'] = 'rb'
            elif has_rigid_sheathing:
                atticd['roofconstype'] = 'ps'
            else:
                atticd['roofconstype'] = 'wf'

            # roof center of cavity R-value
            roof_rvalue = xpath(attic,
                                'sum(h:AtticRoofInsulation/h:Layer[not(boolean(h:InsulationMaterial/h:Rigid) and h:InstallationType="continuous")]/h:NominalRValue)')
            roof_rvalue, atticd['roof_coc_rvalue'] = \
                min(roof_center_of_cavity_rvalues[atticd['roofconstype']][atticd['extfinish']].items(),
                    key=lambda x: abs(x[0] - roof_rvalue))

            # attic floor center of cavity R-value
            attic_floor_rvalue = xpath(attic, 'sum(h:AtticFloorInsulation/h:Layer/h:NominalRValue)')
            atticd['attic_floor_coc_rvalue'] = \
                min(attic_floor_rvalues, key=lambda x: abs(x - attic_floor_rvalue)) + 0.5

        if len(atticds) == 0:
            raise TranslationError('There are no Attic elements in this building.')
        elif len(atticds) <= 2:
            for atticd in atticds:
                atticd['_roofids'] = {atticd['_roofid']}
                del atticd['_roofid']
        elif len(atticds) > 2:
            # If there are more than two attics, combine and average by rooftype.
            attics_by_rooftype = {}
            for atticd in atticds:
                try:
                    attics_by_rooftype[atticd['rooftype']].append(atticd)
                except KeyError:
                    attics_by_rooftype[atticd['rooftype']] = [atticd]

            # Determine predominant roof characteristics for each rooftype.
            attic_keys = ('roofconstype', 'extfinish', 'roofcolor', 'rooftype')
            combined_atticds = []
            for rooftype,atticds in attics_by_rooftype.items():
                combined_atticd = {}

                # Roof Area
                combined_atticd['roof_area'] = sum([atticd['roof_area'] for atticd in atticds])

                # Roof type, roof color, exterior finish, construction type
                for attic_key in ('roofconstype', 'extfinish', 'roofcolor', 'rooftype'):
                    roof_area_by_cat = {}
                    for atticd in atticds:
                        try:
                            roof_area_by_cat[atticd[attic_key]] += atticd['roof_area']
                        except KeyError:
                            roof_area_by_cat[atticd[attic_key]] = atticd['roof_area']
                    combined_atticd[attic_key] = max(roof_area_by_cat, key=lambda x: roof_area_by_cat[x])

                # ids of hpxml roofs along for the ride
                combined_atticd['_roofids'] = set([atticd['_roofid'] for atticd in atticds])

                # Calculate roof area weighted center of cavity R-value
                combined_atticd['roof_coc_rvalue'] = \
                    sum([atticd['roof_coc_rvalue'] * atticd['roof_area'] for atticd in atticds]) / \
                    combined_atticd['roof_area']

                # Calculate attic floor weighted average center-of-cavity R-value
                combined_atticd['attic_floor_coc_rvalue'] = \
                    sum([atticd['attic_floor_coc_rvalue'] * atticd['roof_area'] for atticd in atticds]) / \
                    combined_atticd['roof_area']
                combined_atticds.append(combined_atticd)

            atticds = combined_atticds
            del combined_atticds
            del attics_by_rooftype

        # Order the attic/roofs from largest to smallest
        atticds.sort(key=lambda x: x['roof_area'], reverse=True)

        # Take the largest two
        zone_roof = []
        for i,atticd in enumerate(atticds[0:2], 1):

            # Get Roof R-value
            roffset = roof_center_of_cavity_rvalues[atticd['roofconstype']][atticd['extfinish']][0]
            roof_rvalue = min(roof_center_of_cavity_rvalues[atticd['roofconstype']][atticd['extfinish']].keys(),
                              key=lambda x: abs(atticd['roof_coc_rvalue'] - roffset - x))

            # Get Attic Floor R-value
            attic_floor_rvalue = min(attic_floor_rvalues,
                                     key=lambda x: abs(atticd['attic_floor_coc_rvalue'] - 0.5 - x))

            # store it all
            zone_roof_item = OrderedDict()
            zone_roof_item['roof_name'] = 'roof%d' % i
            zone_roof_item['roof_area'] = atticd['roof_area']
            zone_roof_item['roof_assembly_code'] = 'rf%s%02d%s' % (atticd['roofconstype'], roof_rvalue, atticd['extfinish'])
            zone_roof_item['roof_color'] = atticd['roofcolor']
            zone_roof_item['roof_type'] = atticd['rooftype']
            zone_roof_item['_roofids'] = atticd['_roofids']
            if atticd['rooftype'] != 'cath_ceiling':
                zone_roof_item['ceiling_assembly_code'] = 'ecwf%02d' % attic_floor_rvalue
            zone_roof.append(zone_roof_item)

        return zone_roof

    def _get_skylights(self, b):
        ns = self.ns
        xpath = self.xpath
        skylights = b.xpath('//h:Skylight', namespaces=ns)

        zone_skylight = OrderedDict()

        if len(skylights) == 0:
            zone_skylight['skylight_area'] = 0
            return zone_skylight

        # Get areas, u-factors, and shgcs if they exist
        uvalues, shgcs, areas = map(list, zip(*[[xpath(skylight, 'h:%s/text()' % x)
                                                 for x in ('UFactor', 'SHGC', 'Area')]
                                                for skylight in skylights]))
        if None in areas:
            raise TranslationError('Every skylight needs an area.')
        areas = map(float, areas)
        zone_skylight['skylight_area'] = sum(areas)

        # Remove skylights from the calculation where a uvalue or shgc isn't set.
        idxstoremove = set()
        for i, uvalue in enumerate(uvalues):
            if uvalue is None:
                idxstoremove.add(i)
        for i, shgc in enumerate(shgcs):
            if shgc is None:
                idxstoremove.add(i)
        for i in sorted(idxstoremove, reverse=True):
            uvalues.pop(i)
            shgcs.pop(i)
            areas.pop(i)
        assert len(uvalues) == len(shgcs)
        uvalues = map(float, uvalues)
        shgcs = map(float, shgcs)

        if len(uvalues) > 0:
            # Use an area weighted average of the uvalues, shgcs
            zone_skylight['skylight_method'] = 'custom'
            zone_skylight['skylight_u_value'] = sum(
                [uvalue * area for (uvalue, area) in zip(uvalues, areas)]) / sum(areas)
            zone_skylight['skylight_shgc'] = sum([shgc * area for (shgc, area) in zip(shgcs, areas)]) / sum(areas)
        else:
            # use a construction code
            skylight_type_areas = {}
            for skylight in skylights:
                area = convert_to_type(float, xpath(skylight, 'h:Area/text()'))
                skylight_code = self.get_window_code(skylight)
                try:
                    skylight_type_areas[skylight_code] += area
                except KeyError:
                    skylight_type_areas[skylight_code] = area
            zone_skylight['skylight_method'] = 'code'
            zone_skylight['skylight_code'] = max(skylight_type_areas.items(), key=lambda x: x[1])[0]

        return zone_skylight

    def _get_building_zone_floor(self, b):
        ns = self.ns
        xpath = self.xpath

        # building.zone.zone_floor-------------------------------------------------
        zone_floor = OrderedDict()

        foundations = b.xpath('//h:Foundations/h:Foundation', namespaces=ns)
        # get the Foundation element that covers the largest square footage of the house
        foundation = max(foundations,
                         key=lambda fnd: max([xpath(fnd, 'sum(h:%s/h:Area)' % x) for x in ('Slab', 'FrameFloor')]))

        # Foundation type
        hpxml_foundation_type = xpath(foundation, 'name(h:FoundationType/*)')
        if hpxml_foundation_type == 'Basement':
            bsmtcond = xpath(foundation, 'h:FoundationType/h:Basement/h:Conditioned="true"')
            if bsmtcond:
                zone_floor['foundation_type'] = 'cond_basement'
            else:
                # assumed unconditioned basement if h:Conditioned is missing
                zone_floor['foundation_type'] = 'uncond_basement'
        elif hpxml_foundation_type == 'Crawlspace':
            crawlvented = xpath(foundation, 'h:FoundationType/h:Crawlspace/h:Vented="true"')
            if crawlvented:
                zone_floor['foundation_type'] = 'vented_crawl'
            else:
                # assumes unvented crawlspace if h:Vented is missing.
                zone_floor['foundation_type'] = 'unvented_crawl'
        elif hpxml_foundation_type == 'SlabOnGrade':
            zone_floor['foundation_type'] = 'slab_on_grade'
        elif hpxml_foundation_type == 'Garage':
            zone_floor['foundation_type'] = 'unvented_crawl'
        elif hpxml_foundation_type == 'Ambient':
            zone_floor['foundation_type'] = 'vented_crawl'
        else:
            raise TranslationError('HEScore does not have a foundation type analogous to: %s' % hpxml_foundation_type)

        # Foundation Wall insulation R-value
        fwra = 0
        fwtotalarea = 0
        foundationwalls = foundation.xpath('h:FoundationWall', namespaces=ns)
        fw_eff_rvalues = dict(zip((0, 5, 11, 19), (4, 7.9, 11.6, 19.6)))
        if len(foundationwalls) > 0:
            if zone_floor['foundation_type'] == 'slab_on_grade':
                raise TranslationError('The house is a slab on grade foundation, but has foundation walls.')
            del fw_eff_rvalues[5]  # remove the value for slab insulation
            for fwall in foundationwalls:
                fwarea, fwlength, fwheight = \
                    map(lambda x: convert_to_type(float, xpath(fwall, 'h:%s/text()' % x)),
                        ('Area', 'Length', 'Height'))
                if fwarea is None:
                    try:
                        fwarea = fwlength * fwheight
                    except TypeError:
                        if len(foundationwalls) == 1:
                            fwarea = 1.0
                        else:
                            raise TranslationError(
                                'If there is more than one FoundationWall, an Area is required for each.')
                fwrvalue = xpath(fwall, 'sum(h:Insulation/h:Layer/h:NominalRValue)')
                fweffrvalue = fw_eff_rvalues[min(fw_eff_rvalues.keys(), key=lambda x: abs(fwrvalue - x))]
                fwra += fweffrvalue * fwarea
                fwtotalarea += fwarea
            zone_floor['foundation_insulation_level'] = fwra / fwtotalarea - 4.0
        elif zone_floor['foundation_type'] == 'slab_on_grade':
            del fw_eff_rvalues[11]  # remove unused values
            del fw_eff_rvalues[19]
            slabs = foundation.xpath('h:Slab', namespaces=ns)
            slabra = 0
            slabtotalperimeter = 0
            for slab in slabs:
                exp_perimeter = convert_to_type(float, xpath(slab, 'h:ExposedPerimeter/text()'))
                if exp_perimeter is None:
                    if len(slabs) == 1:
                        exp_perimeter = 1.0
                    else:
                        raise TranslationError(
                            'If there is more than one Slab, an ExposedPerimeter is required for each.')
                slabrvalue = xpath(slab, 'sum(h:PerimeterInsulation/h:Layer/h:NominalRValue)')
                slabeffrvalue = fw_eff_rvalues[min(fw_eff_rvalues.keys(), key=lambda x: abs(slabrvalue - x))]
                slabra += slabeffrvalue * exp_perimeter
                slabtotalperimeter += exp_perimeter
            zone_floor['foundation_insulation_level'] = slabra / slabtotalperimeter - 4.0
        else:
            zone_floor['foundation_insulation_level'] = 0
        zone_floor['foundation_insulation_level'] = min(fw_eff_rvalues.keys(), key=lambda x: abs(
            zone_floor['foundation_insulation_level'] - x))

        # floor above foundation insulation
        ffra = 0
        fftotalarea = 0
        framefloors = foundation.xpath('h:FrameFloor', namespaces=ns)
        floor_eff_rvalues = dict(
            zip((0, 11, 13, 15, 19, 21, 25, 30, 38), (4.0, 15.8, 17.8, 19.8, 23.8, 25.8, 31.8, 37.8, 42.8)))
        if len(framefloors) > 0:
            for framefloor in framefloors:
                ffarea = convert_to_type(float, xpath(framefloor, 'h:Area/text()'))
                if ffarea is None:
                    if len(framefloors) == 1:
                        ffarea = 1.0
                    else:
                        raise TranslationError('If there is more than one FrameFloor, an Area is required for each.')
                ffrvalue = xpath(framefloor, 'sum(h:Insulation/h:Layer/h:NominalRValue)')
                ffeffrvalue = floor_eff_rvalues[min(floor_eff_rvalues.keys(), key=lambda x: abs(ffrvalue - x))]
                ffra += ffarea * ffeffrvalue
                fftotalarea += ffarea
            ffrvalue = ffra / fftotalarea - 4.0
            zone_floor['floor_assembly_code'] = 'efwf%02dca' % min(floor_eff_rvalues.keys(),
                                                                   key=lambda x: abs(ffrvalue - x))
        else:
            zone_floor['floor_assembly_code'] = 'efwf00ca'

        return zone_floor

    def _get_building_zone_wall(self, b, bldg_about):
        xpath = self.xpath
        ns = self.ns
        sidemap = self.sidemap

        # building.zone.zone_wall--------------------------------------------------
        zone_wall = []

        hpxmlwalls = dict([(side, []) for side in sidemap.values()])
        hpxmlwalls['noside'] = []
        for wall in b.xpath('h:BuildingDetails/h:Enclosure/h:Walls/h:Wall', namespaces=ns):
            walld = {'assembly_code': self.get_wall_assembly_code(wall),
                     'area': convert_to_type(float, xpath(wall, 'h:Area/text()')),
                     'id': xpath(wall, 'h:SystemIdentifier/@id')}

            try:
                wall_azimuth = self.get_nearest_azimuth(xpath(wall, 'h:Azimuth/text()'),
                                                        xpath(wall, 'h:Orientation/text()'))
            except TranslationError:
                # There is no directional information in the HPXML wall
                wall_side = 'noside'
                hpxmlwalls[wall_side].append(walld)
            else:
                try:
                    wall_side = sidemap[wall_azimuth]
                except KeyError:
                    # The direction of the wall is in between sides
                    # split the area between sides
                    walld['area'] /= 2.0
                    hpxmlwalls[sidemap[unspin_azimuth(wall_azimuth + 45)]].append(dict(walld))
                    hpxmlwalls[sidemap[unspin_azimuth(wall_azimuth - 45)]].append(dict(walld))
                else:
                    hpxmlwalls[wall_side].append(walld)

        if len(hpxmlwalls['noside']) > 0 and map(len, [hpxmlwalls[key] for key in sidemap.values()]) == ([0] * 4):
            # if none of the walls have orientation information
            # copy the walls to all sides
            for side in sidemap.values():
                hpxmlwalls[side] = hpxmlwalls['noside']
            del hpxmlwalls['noside']
        else:
            # make sure all of the walls have an orientation
            if len(hpxmlwalls['noside']) > 0:
                raise TranslationError('Some of the HPXML walls have orientation information and others do not.')

        # Wall effective R-value map
        wall_const_types = ('wf', 'ps', 'ov', 'br', 'cb', 'sb')
        wall_ext_finish_types = ('wo', 'st', 'vi', 'al', 'br', 'nn')
        wall_eff_rvalues = {}
        wall_eff_rvalues['wf'] = dict(zip(wall_ext_finish_types[:-1], [dict(zip((0, 3, 7, 11, 13, 15, 19, 21), x))
                                                                       for x in
                                                                       [(3.6, 5.7, 9.7, 13.7, 15.7, 17.7, 21.7, 23.7),
                                                                        (2.3, 4.4, 8.4, 12.4, 14.4, 16.4, 20.4, 22.4),
                                                                        (2.2, 4.3, 8.3, 12.3, 14.3, 16.3, 20.3, 22.3),
                                                                        (2.1, 4.2, 8.2, 12.2, 14.2, 16.2, 20.2, 22.2),
                                                                        (
                                                                            2.9, 5.0, 9.0, 13.0, 15.0, 17.0, 21.0, 23.0)]]))
        wall_eff_rvalues['ps'] = dict(zip(wall_ext_finish_types[:-1], [dict(zip((11, 13, 15, 19, 21), x))
                                                                       for x in [(17.1, 19.1, 21.1, 25.1, 27.1),
                                                                                 (16.4, 18.4, 20.4, 24.4, 26.4),
                                                                                 (16.3, 18.3, 20.3, 24.3, 26.3),
                                                                                 (16.2, 18.2, 20.2, 24.2, 26.2),
                                                                                 (17.0, 19.0, 21.0, 25.0, 27.0)]]))
        wall_eff_rvalues['ov'] = dict(zip(wall_ext_finish_types[:-1], [dict(zip((19, 21, 27, 33, 38), x))
                                                                       for x in [(21.0, 23.0, 29.0, 35.0, 40.0),
                                                                                 (20.3, 22.3, 28.3, 34.3, 39.3),
                                                                                 (20.1, 22.1, 28.1, 34.1, 39.1),
                                                                                 (20.1, 22.1, 28.1, 34.1, 39.1),
                                                                                 (20.9, 22.9, 28.9, 34.9, 39.9)]]))
        wall_eff_rvalues['br'] = {'nn': dict(zip((0, 5, 10), (2.9, 7.9, 12.8)))}
        wall_eff_rvalues['cb'] = dict(zip(('st', 'br', 'nn'), [dict(zip((0, 3, 6), x))
                                                               for x in [(4.1, 5.7, 8.5),
                                                                         (5.6, 7.2, 10),
                                                                         (4, 5.6, 8.3)]]))
        wall_eff_rvalues['sb'] = {'st': {0: 58.8}}

        # build HEScore walls
        for side in sidemap.values():
            heswall = OrderedDict()
            heswall['side'] = side
            if len(hpxmlwalls[side]) == 1 and hpxmlwalls[side][0]['area'] is None:
                hpxmlwalls[side][0]['area'] = 1.0
            elif len(hpxmlwalls[side]) > 1 and None in [x['area'] for x in hpxmlwalls[side]]:
                raise TranslationError('The %s side of the house has %d walls and they do not all have areas.' % (
                    side, len(hpxmlwalls[side])))
            wall_const_type_areas = dict(zip(wall_const_types, [0] * len(wall_const_types)))
            wall_ext_finish_areas = dict(zip(wall_ext_finish_types, [0] * len(wall_ext_finish_types)))
            wallra = 0
            walltotalarea = 0
            for walld in hpxmlwalls[side]:
                const_type = walld['assembly_code'][2:4]
                ext_finish = walld['assembly_code'][6:8]
                rvalue = int(walld['assembly_code'][4:6])
                eff_rvalue = wall_eff_rvalues[const_type][ext_finish][rvalue]
                wallra += walld['area'] * eff_rvalue
                walltotalarea += walld['area']
                wall_const_type_areas[const_type] += walld['area']
                wall_ext_finish_areas[ext_finish] += walld['area']
            const_type = max(wall_const_type_areas.keys(), key=lambda x: wall_const_type_areas[x])
            ext_finish = max(wall_ext_finish_areas.keys(), key=lambda x: wall_ext_finish_areas[x])
            try:
                roffset = wall_eff_rvalues[const_type][ext_finish][0]
            except KeyError:
                rvalue, eff_rvalue = min(wall_eff_rvalues[const_type][ext_finish].items(), key=lambda x: x[0])
                roffset = eff_rvalue - rvalue
            # if const_type == 'ps':
            #                 roffset += 4.16
            rvalueavgeff = wallra / walltotalarea
            rvalueavgnom = rvalueavgeff - roffset
            comb_rvalue = min(wall_eff_rvalues[const_type][ext_finish].keys(),
                              key=lambda x: abs(rvalueavgnom - x))
            heswall['wall_assembly_code'] = 'ew%s%02d%s' % (const_type, comb_rvalue, ext_finish)
            zone_wall.append(heswall)

        # building.zone.zone_wall.zone_window--------------------------------------
        # Assign each window to a side of the house
        hpxmlwindows = dict([(side, []) for side in sidemap.values()])
        for hpxmlwndw in b.xpath('h:BuildingDetails/h:Enclosure/h:Windows/h:Window', namespaces=ns):

            # Get the area, uvalue, SHGC, or window_code
            windowd = {'area': convert_to_type(float, xpath(hpxmlwndw, 'h:Area/text()'))}

            # Make sure every window has an area
            if windowd['area'] is None:
                raise TranslationError('All windows need an area.')

            qty = convert_to_type(int, xpath(hpxmlwndw, 'h:Quantity/text()'))
            if isinstance(qty, int):
                windowd['area'] *= qty
            windowd['uvalue'] = convert_to_type(float, xpath(hpxmlwndw, 'h:UFactor/text()'))
            windowd['shgc'] = convert_to_type(float, xpath(hpxmlwndw, 'h:SHGC/text()'))
            if windowd['uvalue'] is not None and windowd['shgc'] is not None:
                windowd['window_code'] = None
            else:
                windowd['window_code'] = self.get_window_code(hpxmlwndw)

            # Window side
            window_sides = []
            attached_to_wall_id = xpath(hpxmlwndw, 'h:AttachedToWall/@idref')
            if attached_to_wall_id is not None:
                # Give preference to the Attached to Wall element to determine the side of the house.
                for side, walls in hpxmlwalls.items():
                    for wall in walls:
                        if attached_to_wall_id == wall['id']:
                            window_sides.append(side)
                            break
            else:
                # If there's not Attached to Wall element, figure it out from the Azimuth/Orientation
                try:
                    wndw_azimuth = self.get_nearest_azimuth(xpath(hpxmlwndw, 'h:Azimuth/text()'),
                                                            xpath(hpxmlwndw, 'h:Orientation/text()'))
                    window_sides = [sidemap[wndw_azimuth]]
                except TranslationError:
                    # there's no directional information in the window
                    raise TranslationError(
                        'All windows need to have either an AttachedToWall, Orientation, or Azimuth sub element.')
                else:
                    try:
                        window_sides = [sidemap[wndw_azimuth]]
                    except KeyError:
                        # the direction of the window is between sides, split area
                        window_sides = [sidemap[unspin_azimuth(wndw_azimuth + x)] for x in (-45, 45)]

            # Assign properties and areas to the correct side of the house
            for window_side in window_sides:
                windowd['area'] /= float(len(window_sides))
                hpxmlwindows[window_side].append(dict(windowd))

        # Make sure the windows aren't on shared walls if it's a townhouse.
        def windows_are_on_shared_walls():
            shared_wall_sides = set(sidemap.values()) - set(bldg_about['town_house_walls'].split('_'))
            for side in shared_wall_sides:
                if len(hpxmlwindows[side]) > 0:
                    return True
            return False

        if bldg_about['shape'] == 'town_house':
            window_on_shared_wall_fail = windows_are_on_shared_walls()
            if window_on_shared_wall_fail:
                if bldg_about['town_house_walls'] == 'back_right_front':
                    bldg_about['town_house_walls'] = 'back_front_left'
                    window_on_shared_wall_fail = windows_are_on_shared_walls()
            if window_on_shared_wall_fail:
                raise TranslationError('The house has windows on shared walls.')

        # Determine the predominant window characteristics and create HEScore windows
        for side, windows in hpxmlwindows.items():

            # Add to the correct wall
            for heswall in zone_wall:
                if heswall['side'] == side:
                    break

            zone_window = OrderedDict()
            heswall['zone_window'] = zone_window

            # If there are no windows on that side of the house
            if len(windows) == 0:
                zone_window['window_area'] = 0
                zone_window['window_method'] = 'code'
                zone_window['window_code'] = 'scna'
                continue

            # Get the list of uvalues and shgcs for the windows on this side of the house.
            uvalues, shgcs, areas = map(list,
                                        zip(*[[window[x] for x in ('uvalue', 'shgc', 'area')] for window in windows]))

            zone_window['window_area'] = sum(areas)

            # Remove windows from the calculation where a uvalue or shgc isn't set.
            idxstoremove = set()
            for i, uvalue in enumerate(uvalues):
                if uvalue is None:
                    idxstoremove.add(i)
            for i, shgc in enumerate(shgcs):
                if shgc is None:
                    idxstoremove.add(i)
            for i in sorted(idxstoremove, reverse=True):
                uvalues.pop(i)
                shgcs.pop(i)
                areas.pop(i)
            assert len(uvalues) == len(shgcs)

            if len(uvalues) > 0:
                # Use an area weighted average of the uvalues, shgcs
                zone_window['window_method'] = 'custom'
                zone_window['window_u_value'] = sum([uvalue * area for (uvalue, area) in zip(uvalues, areas)]) / sum(
                    areas)
                zone_window['window_shgc'] = sum([shgc * area for (shgc, area) in zip(shgcs, areas)]) / sum(areas)
            else:
                # Use a window construction code
                zone_window['window_method'] = 'code'
                # Use the properties of the largest window on the side
                window_code_areas = {}
                for window in windows:
                    assert window['window_code'] is not None
                    try:
                        window_code_areas[window['window_code']] += window['area']
                    except KeyError:
                        window_code_areas[window['window_code']] = window['area']
                zone_window['window_code'] = max(window_code_areas.items(), key=lambda x: x[1])[0]

        return zone_wall

    eff_method_map = {'user': 'efficiency', 'shipment_weighted': 'year'}

    def _get_systems_heating(self, b):
        xpath = self.xpath
        ns = self.ns

        sys_heating = OrderedDict()

        # Use the primary heating system specified in the HPXML file if that element exists.
        primaryhtgsys = xpath(b,
                              '//h:HVACPlant/*[//h:HVACPlant/h:PrimarySystems/h:PrimaryHeatingSystem/@idref=h:SystemIdentifier/@id]')

        if primaryhtgsys is None:
            # A primary heating system isn't specified, get the properties of all of them
            htgsystems = []
            has_htgsys_translation_err = False
            for htgsys in b.xpath('//h:HVACPlant/h:HeatingSystem|//h:HVACPlant/h:HeatPump', namespaces=ns):
                try:
                    htgsysd = self.get_heating_system_type(htgsys)
                except TranslationError as ex:
                    has_htgsys_translation_err = True
                    continue
                else:
                    htgsystems.append(htgsysd)
            if has_htgsys_translation_err and len(htgsystems) == 0:
                raise ex
        else:
            htgsystems = [self.get_heating_system_type(primaryhtgsys)]

        capacities = [x['capacity'] for x in htgsystems]
        if None in capacities:
            if len(capacities) == 1:
                htgsystems[0]['capacity'] = 1.0
            else:
                raise TranslationError(
                    'If a primary heating system is not defined, each heating system must have a capacity')

        htgsys_by_capacity = {}
        htgsys_groupby_keys = ('type', 'fuel_primary', 'efficiency_method')
        for htgsysd in htgsystems:
            try:
                combhtgsys = htgsys_by_capacity[tuple([htgsysd[x] for x in htgsys_groupby_keys])]
            except KeyError:
                combhtgsys = {'totalcapacity': 0, 'n': 0, 'sum': 0}
                for key in htgsys_groupby_keys:
                    combhtgsys[key] = htgsysd[key]
                htgsys_by_capacity[tuple([htgsysd[x] for x in htgsys_groupby_keys])] = combhtgsys

            combhtgsys['totalcapacity'] += htgsysd['capacity']
            if combhtgsys['efficiency_method'] is not None:
                combhtgsys['sum'] += htgsysd[self.eff_method_map[combhtgsys['efficiency_method']]] * htgsysd['capacity']
            combhtgsys['n'] += 1

        for combhtgsys in htgsys_by_capacity.values():
            if combhtgsys['efficiency_method'] == 'user':
                if combhtgsys['type'] in ('heat_pump', 'gchp'):
                    htg_round_decimal_places = 1
                else:
                    htg_round_decimal_places = 2
                combhtgsys[self.eff_method_map[combhtgsys['efficiency_method']]] = round(
                    combhtgsys['sum'] / combhtgsys['totalcapacity'], htg_round_decimal_places)
            elif combhtgsys['efficiency_method'] == 'shipment_weighted':
                combhtgsys[self.eff_method_map[combhtgsys['efficiency_method']]] = int(
                    round(combhtgsys['sum'] / combhtgsys['totalcapacity']))
            else:
                assert combhtgsys['efficiency_method'] is None
                del combhtgsys['efficiency_method']
            del combhtgsys['sum']
            del combhtgsys['n']

        if len(htgsys_by_capacity) > 0:
            sys_heating.update(max(htgsys_by_capacity.values(), key=lambda x: x['totalcapacity']))
            del sys_heating['totalcapacity']
            if sys_heating['efficiency_method'] == 'shipment_weighted' and sys_heating['year'] < 1970:
                sys_heating['year'] = 1970
        else:
            sys_heating = {'type': 'none'}

        return sys_heating

    def _get_systems_cooling(self, b):
        xpath = self.xpath
        ns = self.ns
        sys_cooling = OrderedDict()

        primaryclgsys = xpath(b,
                              '//h:HVACPlant/*[//h:HVACPlant/h:PrimarySystems/h:PrimaryCoolingSystem/@idref=h:SystemIdentifier/@id]')

        if primaryclgsys is None:
            # A primary cooling system isn't specified, get the properties of all of them.
            clgsystems = []
            has_clgsys_translation_err = False
            for clgsys in b.xpath('//h:HVACPlant/h:CoolingSystem|//h:HVACPlant/h:HeatPump', namespaces=ns):
                try:
                    clgsysd = self.get_cooling_system_type(clgsys)
                except TranslationError as ex:
                    has_clgsys_translation_err = True
                    continue
                else:
                    clgsystems.append(clgsysd)
            if has_clgsys_translation_err and len(clgsystems) == 0:
                raise ex
        else:
            clgsystems = [self.get_cooling_system_type(primaryclgsys)]

        capacities = [x['capacity'] for x in clgsystems]
        if None in capacities:
            if len(capacities) == 1:
                clgsystems[0]['capacity'] = 1.0
            else:
                raise TranslationError(
                    'If a primary cooling system is not defined, each cooling system must have a capacity')

        clgsys_by_capacity = {}
        clgsys_groupby_keys = ('type', 'efficiency_method')
        for clgsysd in clgsystems:
            try:
                combclgsys = clgsys_by_capacity[tuple([clgsysd[x] for x in clgsys_groupby_keys])]
            except KeyError:
                combclgsys = {'totalcapacity': 0, 'n': 0, 'sum': 0}
                for key in clgsys_groupby_keys:
                    combclgsys[key] = clgsysd[key]
                clgsys_by_capacity[tuple([clgsysd[x] for x in clgsys_groupby_keys])] = combclgsys

            combclgsys['totalcapacity'] += clgsysd['capacity']
            combclgsys['sum'] += clgsysd[self.eff_method_map[combclgsys['efficiency_method']]] * clgsysd['capacity']
            combclgsys['n'] += 1

        for combclgsys in clgsys_by_capacity.values():
            if combclgsys['efficiency_method'] == 'user':
                combclgsys[self.eff_method_map[combclgsys['efficiency_method']]] = round(
                    combclgsys['sum'] / combclgsys['totalcapacity'], 1)
            else:
                assert combclgsys['efficiency_method'] == 'shipment_weighted'
                combclgsys[self.eff_method_map[combclgsys['efficiency_method']]] = int(
                    round(combclgsys['sum'] / combclgsys['totalcapacity']))
            del combclgsys['sum']
            del combclgsys['n']

        if len(clgsys_by_capacity) > 0:
            sys_cooling.update(max(clgsys_by_capacity.values(), key=lambda x: x['totalcapacity']))
            del sys_cooling['totalcapacity']
            if sys_cooling['efficiency_method'] == 'shipment_weighted' and sys_cooling['year'] < 1970:
                sys_cooling['year'] = 1970
        else:
            sys_cooling = {'type': 'none'}

        return sys_cooling

    def _get_systems_hvac_distribution(self, b):
        ns = self.ns
        xpath = self.xpath

        hvac_distribution = []
        duct_location_map = {'conditioned space': 'cond_space',
                             'unconditioned space': None,
                             'unconditioned basement': 'uncond_basement',
                             'unvented crawlspace': 'unvented_crawl',
                             'vented crawlspace': 'vented_crawl',
                             'crawlspace': None,
                             'unconditioned attic': 'uncond_attic',
                             'interstitial space': None,
                             'garage': None,
                             'outside': None}
        airdistributionxpath = '//h:HVACDistribution/h:DistributionSystemType/h:AirDistribution'
        allhave_cfaserved = True
        allmissing_cfaserved = True
        airdistsystems_ductfracs = []
        hescore_ductloc_has_ins = {}
        airdistsys_issealed = []
        for airdistsys in b.xpath(airdistributionxpath, namespaces=ns):
            airdistsys_ductfracs = {}
            airdistsys_issealed.append(airdistsys.xpath(
                '(h:DuctLeakageMeasurement/h:LeakinessObservedVisualInspection="connections sealed w mastic") or (ancestor::h:HVACDistribution/h:HVACDistributionImprovement/h:DuctSystemSealed="true")',
                namespaces=ns))
            for duct in airdistsys.xpath('h:Ducts', namespaces=ns):
                frac_duct_area = float(xpath(duct, 'h:FractionDuctArea/text()'))
                hpxml_duct_location = xpath(duct, 'h:DuctLocation/text()')
                hescore_duct_location = duct_location_map[hpxml_duct_location]
                if hescore_duct_location is None:
                    raise TranslationError('No comparable duct location in HEScore: %s' % hpxml_duct_location)
                try:
                    airdistsys_ductfracs[hescore_duct_location] += frac_duct_area
                except KeyError:
                    airdistsys_ductfracs[hescore_duct_location] = frac_duct_area
                duct_has_ins = duct.xpath('h:DuctInsulationRValue > 0 or h:DuctInsulationThickness > 0',
                                          namespaces=ns)
                try:
                    hescore_ductloc_has_ins[hescore_duct_location] = hescore_ductloc_has_ins[
                                                                         hescore_duct_location] or duct_has_ins
                except KeyError:
                    hescore_ductloc_has_ins[hescore_duct_location] = duct_has_ins
            total_duct_frac = sum(airdistsys_ductfracs.values())
            airdistsys_ductfracs = dict([(key, value / total_duct_frac)
                                         for key, value
                                         in airdistsys_ductfracs.items()])
            cfaserved = xpath(airdistsys.getparent().getparent(), 'h:ConditionedFloorAreaServed/text()')
            if cfaserved is not None:
                cfaserved = float(cfaserved)
                airdistsys_ductfracs = dict(
                    [(key, value * cfaserved) for key, value in airdistsys_ductfracs.items()])
                allmissing_cfaserved = False
            else:
                allhave_cfaserved = False
            airdistsystems_ductfracs.append(airdistsys_ductfracs)
        allsame_cfaserved = allhave_cfaserved or allmissing_cfaserved

        # Combine all
        ductfracs = {}
        issealedfracs = {}
        if (len(airdistsystems_ductfracs) > 1 and allsame_cfaserved) or len(airdistsystems_ductfracs) <= 1:
            for airdistsys_ductfracs, issealed in zip(airdistsystems_ductfracs, airdistsys_issealed):
                for key, value in airdistsys_ductfracs.items():
                    try:
                        ductfracs[key] += value
                    except KeyError:
                        ductfracs[key] = value
                    try:
                        issealedfracs[key] += value * float(issealed)
                    except KeyError:
                        issealedfracs[key] = value * float(issealed)

        else:
            raise TranslationError(
                'All HVACDistribution elements need to have or NOT have the ConditionFloorAreaServed subelement.')

            # Make sure there are only three locations and normalize to percentages
        top3locations = sorted(ductfracs.keys(), key=lambda x: ductfracs[x], reverse=True)[0:3]
        for location in ductfracs.keys():
            if location not in top3locations:
                del ductfracs[location]
                del hescore_ductloc_has_ins[location]
                del issealedfracs[location]
        issealedfracs = dict([(key, bool(round(x / ductfracs[key]))) for key, x in issealedfracs.items()])
        normalization_denominator = sum(ductfracs.values())
        ductfracs = dict([(key, int(round(x / normalization_denominator * 100))) for key, x in ductfracs.items()])
        # Sometimes with the rounding it adds up to a number slightly off of 100, adjust the largest fraction to make it add up to 100
        if len(top3locations) > 0:
            ductfracs[top3locations[0]] += 100 - sum(ductfracs.values())

        for i, location in enumerate(top3locations, 1):
            hvacd = OrderedDict()
            hvacd['name'] = 'duct%d' % i
            hvacd['location'] = location
            hvacd['fraction'] = ductfracs[location]
            hvacd['insulated'] = hescore_ductloc_has_ins[location]
            hvacd['sealed'] = issealedfracs[location]
            hvac_distribution.append(hvacd)

        return hvac_distribution

    def _get_systems_dhw(self, b, sys_heating):
        ns = self.ns
        xpath = self.xpath

        sys_dhw = OrderedDict()

        water_heating_systems = xpath(b, '//h:WaterHeatingSystem')
        if isinstance(water_heating_systems, list):
            dhwfracs = map(lambda x: None if x is None else float(x),
                           [xpath(water_heating_system, 'h:FractionDHWLoadServed/text()') for water_heating_system in
                            water_heating_systems])
            if None in dhwfracs:
                primarydhw = water_heating_systems[0]
            else:
                primarydhw = max(zip(water_heating_systems, dhwfracs), key=lambda x: x[1])[0]
        elif water_heating_systems is None:
            raise TranslationError('No water heating systems found.')
        else:
            primarydhw = water_heating_systems
        water_heater_type = xpath(primarydhw, 'h:WaterHeaterType/text()')
        if water_heater_type in ('storage water heater', 'instantaneous water heater'):
            sys_dhw['category'] = 'unit'
            sys_dhw['type'] = 'storage'
            sys_dhw['fuel_primary'] = self.fuel_type_mapping[xpath(primarydhw, 'h:FuelType/text()')]
        elif water_heater_type == 'space-heating boiler with storage tank':
            sys_dhw['category'] = 'combined'
            sys_dhw['type'] = 'indirect'
            if sys_heating['type'] != 'boiler':
                raise TranslationError(
                    'Cannot have an indirect water heater if the primary heating system is not a boiler.')
        elif water_heater_type == 'space-heating boiler with tankless coil':
            sys_dhw['category'] = 'combined'
            sys_dhw['type'] = 'tankless_coil'
            if sys_heating['type'] != 'boiler':
                raise TranslationError(
                    'Cannot have a tankless coil water heater if the primary heating system is not a boiler.')
        elif water_heater_type == 'heat pump water heater':
            sys_dhw['category'] = 'unit'
            sys_dhw['type'] = 'heat_pump'
            sys_dhw['fuel_primary'] = 'electric'
        else:
            raise TranslationError('HEScore cannot model the water heater type: %s' % water_heater_type)

        if not sys_dhw['category'] == 'combined':
            energyfactor = xpath(primarydhw, 'h:EnergyFactor/text()')
            if energyfactor is not None:
                sys_dhw['efficiency_method'] = 'user'
                sys_dhw['energy_factor'] = round(float(energyfactor), 2)
            else:
                dhwyear = int(xpath(primarydhw, '(h:YearInstalled|h:ModelYear)[1]/text()'))
                if dhwyear < 1972:
                    dhwyear = 1972
                sys_dhw['efficiency_method'] = 'shipment_weighted'
                sys_dhw['year'] = dhwyear
        return sys_dhw

    def _validate_hescore_inputs(self, hescore_inputs):

        def do_bounds_check(fieldname, value, minincl, maxincl):
            if value < minincl or value > maxincl:
                raise InputOutOfBounds(fieldname, value)

        this_year = dt.datetime.today().year

        do_bounds_check('assessment_date',
                        dt.datetime.strptime(hescore_inputs['building']['about']['assessment_date'], '%Y-%m-%d').date(),
                        dt.date(2010, 1, 1), dt.datetime.today().date())

        do_bounds_check('year_built',
                        hescore_inputs['building']['about']['year_built'],
                        1600, this_year)

        do_bounds_check('number_bedrooms',
                        hescore_inputs['building']['about']['number_bedrooms'],
                        1, 10)

        do_bounds_check('num_floor_above_grade',
                        hescore_inputs['building']['about']['num_floor_above_grade'],
                        1, 4)

        do_bounds_check('floor_to_ceiling_height',
                        hescore_inputs['building']['about']['floor_to_ceiling_height'],
                        6, 12)

        do_bounds_check('conditioned_floor_area',
                        hescore_inputs['building']['about']['conditioned_floor_area'],
                        250, 25000)

        if hescore_inputs['building']['about']['blower_door_test']:
            do_bounds_check('envelope_leakage',
                            hescore_inputs['building']['about']['envelope_leakage'],
                            0, 25000)

        for zone_roof in hescore_inputs['building']['zone']['zone_roof']:
            zone_skylight = zone_roof['zone_skylight']
            do_bounds_check('skylight_area',
                            zone_skylight['skylight_area'],
                            0, 300)

            if zone_skylight['skylight_area'] > 0 and zone_skylight['skylight_method'] == 'custom':
                do_bounds_check('skylight_u_value',
                                zone_skylight['skylight_u_value'],
                                0.01, 5)
                do_bounds_check('skylight_shgc',
                                zone_skylight['skylight_shgc'],
                                0, 1)

        do_bounds_check('foundation_insulation_level',
                        hescore_inputs['building']['zone']['zone_floor']['foundation_insulation_level'],
                        0, 19)

        for zone_wall in hescore_inputs['building']['zone']['zone_wall']:
            zone_window = zone_wall['zone_window']
            do_bounds_check('window_area',
                            zone_window['window_area'],
                            0, 999)
            if zone_window['window_area'] > 0 and zone_window['window_method'] == 'custom':
                do_bounds_check('window_u_value',
                                zone_window['window_u_value'],
                                0.01, 5)
                do_bounds_check('window_shgc',
                                zone_window['window_shgc'],
                                0, 1)

        sys_heating = hescore_inputs['building']['systems']['heating']
        if sys_heating['efficiency_method'] == 'user':
            do_bounds_check('heating_efficiency',
                            sys_heating['efficiency'],
                            0.1, 20)
        else:
            assert sys_heating['efficiency_method'] == 'shipment_weighted'
            do_bounds_check('heating_year',
                            sys_heating['year'],
                            1970, this_year)

        sys_cooling = hescore_inputs['building']['systems']['cooling']
        if sys_cooling['efficiency_method'] == 'user':
            do_bounds_check('cooling_efficiency',
                            sys_cooling['efficiency'],
                            0.1, 30)
        else:
            assert sys_cooling['efficiency_method'] == 'shipment_weighted'
            do_bounds_check('cooling_year',
                            sys_cooling['year'],
                            1970, this_year)

        for hvacd in hescore_inputs['building']['systems']['hvac_distribution']:
            do_bounds_check('hvac_distribution_fraction',
                            hvacd['fraction'],
                            0, 100)

        dhw = hescore_inputs['building']['systems']['domestic_hot_water']
        if dhw['type'] in ('storage', 'heat_pump'):
            if dhw['efficiency_method'] == 'user':
                do_bounds_check('domestic_hot_water_energy_factor',
                                dhw['energy_factor'],
                                0.1, 3.0)
            else:
                assert dhw['efficiency_method'] == 'shipment_weighted'
                do_bounds_check('domestic_hot_water_year',
                                dhw['year'],
                                1972, this_year)


def main():
    parser = argparse.ArgumentParser(description='Convert HPXML v1.1.1 or v2.x files to HEScore inputs')
    parser.add_argument('hpxml_input', type=argparse.FileType('r'), help='Filename of hpxml file')
    parser.add_argument('-o', '--output', type=argparse.FileType('w'), default=sys.stdout,
                        help='Filename of output file in json format. If not provided, will go to stdout.')
    parser.add_argument('--bldgid',
                        help='HPXML building id to score if there are more than one <Building/> elements. Default: first one.')
    parser.add_argument('--nrelassumptions', action='store_true',
                        help='Use the NREL assumptions to guess at data elements that are missing.')

    args = parser.parse_args()

    try:
        t = HPXMLtoHEScoreTranslator(args.hpxml_input)
        t.hpxml_to_hescore_json(args.output, hpxml_bldg_id=args.bldgid, nrel_assumptions=args.nrelassumptions)
    except HPXMLtoHEScoreError as ex:
        exclass = type(ex).__name__
        exmsg = ex.message
        logging.error('%s:%s', exclass, exmsg)
        sys.exit(1)


if __name__ == '__main__':
    main()