from .base import HPXMLtoHEScoreTranslatorBase
from collections import OrderedDict
from .exceptions import TranslationError


def convert_to_type(type_, value):
    if value is None:
        return value
    else:
        return type_(value)


class HPXML3toHEScoreTranslator(HPXMLtoHEScoreTranslatorBase):
    SCHEMA_DIR = 'hpxml-3.0.0'

    def check_hpwes(self, v2_p, b):
        # multiple verification nodes?
        return self.xpath(b, 'h:BuildingDetails/h:GreenBuildingVerifications/h:GreenBuildingVerification/h:Type="Home '
                             'Performance with ENERGY STAR"')

    def sort_foundations(self, fnd, b):
        # Sort the foundations from largest area to smallest
        def get_fnd_area(fnd):
            attached_ids = OrderedDict()
            attached_ids['Slab'] = self.xpath(fnd, 'h:AttachedToSlab/@idref')
            attached_ids['FrameFloor'] = self.xpath(fnd, 'h:AttachedToFrameFloor/@idref')
            return max(
                [self.xpath(b, 'sum(//h:{}[contains("{}", h:SystemIdentifier/@id)]/h:Area)'.format(key, value)) for
                 key, value in attached_ids.items()])

        fnd.sort(key=get_fnd_area, reverse=True)
        return fnd, get_fnd_area

    def get_foundation_walls(self, fnd, b):
        attached_ids = self.xpath(fnd, 'h:AttachedToFoundationWall/@idref')
        foundationwalls = self.xpath(b, '//h:FoundationWall[contains("{}", h:SystemIdentifier/@id)]'.
                                     format(attached_ids), aslist=True)
        return foundationwalls

    def get_foundation_slabs(self, fnd, b):
        attached_ids = self.xpath(fnd, 'h:AttachedToSlab/@idref')
        slabs = self.xpath(b, '//h:Slab[contains("{}", h:SystemIdentifier/@id)]'.format(attached_ids), raise_err=True,
                           aslist=True)
        return slabs

    def get_foundation_frame_floors(self, fnd, b):
        attached_ids = self.xpath(fnd, 'h:AttachedToFrameFloor/@idref')
        frame_floors = self.xpath(b, '//h:FrameFloor[contains("{}",h:SystemIdentifier/@id)]'.format(attached_ids),
                                  aslist=True)
        return frame_floors

    def attic_has_rigid_sheathing(self, v2_attic, roof):
        return self.xpath(roof,
                          'boolean(h:Insulation/h:Layer[h:NominalRValue > 0][h:InstallationType="continuous"]['
                          'boolean(h:InsulationMaterial/h:Rigid)])'
                          # noqa: E501
                          )

    def get_attic_roof_rvalue(self, v2_attic, roof):
        return self.xpath(roof,
                          'sum(h:Insulation/h:Layer/h:NominalRValue)')

    def get_attic_knee_walls(self, attic, b):
        knee_walls = []
        for kneewall_idref in self.xpath(attic, 'h:AttachedToWall/@idref', aslist=True):
            wall = self.xpath(
                b,
                '//h:Wall[h:SystemIdentifier/@id=$kneewallid][h:AtticWallType="knee wall"]',
                raise_err=True,
                kneewallid=kneewall_idref
            )
            wall_rvalue = self.xpath(wall, 'sum(h:Insulation/h:Layer/h:NominalRValue)')
            wall_area = self.xpath(wall, 'h:Area/text()')
            if wall_area is None:
                raise TranslationError('All attic knee walls need an Area specified')
            wall_area = float(wall_area)
            knee_walls.append({'area': wall_area, 'rvalue': wall_rvalue})

        return knee_walls

    def get_attic_type(self, attic, atticid):
        if self.xpath(attic,
                      'h:AtticType/h:Attic/h:CapeCod or boolean(h:AtticType/h:FlatRoof) or boolean('
                      'h:AtticType/h:CathedralCeiling)'):  # noqa: E501
            return 'cath_ceiling'
        elif self.xpath(attic, 'boolean(h:AtticType/h:Attic/h:Conditioned)'):
            return 'cond_attic'
        elif self.xpath(attic, 'boolean(h:AtticType/h:Attic)'):
            return 'vented_attic'
        else:
            raise TranslationError(
                'Attic {}: Cannot translate HPXML AtticType to HEScore rooftype.'.format(atticid))

    def get_attic_floor_rvalue(self, attic, b):
        floor_idref = self.xpath(attic, 'h:AttachedToFrameFloor/@idref')
        # No frame floor attached
        if floor_idref is None:
            return 0.0
        frame_floors = self.xpath(b, '//h:FrameFloor[contains("{}",h:SystemIdentifier/@id)]'.format(floor_idref),
                                  aslist=True, raise_err=True)

        frame_floor_dict_ls = []
        for frame_floor in frame_floors:
            floor_area = self.xpath(frame_floor, 'h:Area/text()')
            rvalue = convert_to_type(float, self.xpath(frame_floor, 'sum(h:Insulation/h:Layer/h:NominalRValue)'))
            if floor_area is None:
                if len(frame_floors) == 1:
                    return rvalue
                else:
                    raise TranslationError('If there are more than one attic frame floor specified, '
                                           'each attic frame floor needs an Area specified')
            frame_floor_dict_ls.append({'area': convert_to_type(float, floor_area), 'rvalue': rvalue})

        try:
            floor_r = sum(x['area'] for x in frame_floor_dict_ls) / \
                      sum(x['area'] / x['rvalue'] for x in frame_floor_dict_ls)
        except ZeroDivisionError:
            floor_r = 0

        return floor_r

    def get_attic_area(self, attic, is_one_roof, footprint_area, roofs):
        # Otherwise, get area from roof element
        area = 0.0
        for roof in roofs:
            roof_area = self.xpath(roof, 'h:Area/text()')
            if roof_area is None:
                if is_one_roof:
                    return footprint_area
                else:
                    raise TranslationError('If there are more than one Attic elements, each needs an area. '
                                           'Please specify under its attached roof element: Roof/Area.')
            area += convert_to_type(float, roof_area)
        return area

    def get_attic_roof_area(self, roof):
        return self.xpath(roof, 'h:Area/text()')

    def get_sunscreen(self, wndw_skylight):
        return bool(self.xpath(wndw_skylight, 'h:ExteriorShading/h:Type/text()') == 'solar screens')

    def get_hescore_walls(self, b):
        return self.xpath(b,
                          'h:BuildingDetails/h:Enclosure/h:Walls/h:Wall[h:ExteriorAdjacentTo="outside" or not('
                          'h:ExteriorAdjacentTo)]',
                          # noqa: E501
                          aslist=True)

    def check_is_doublepane(self, window, glass_layers):
        return (self.xpath(window, 'h:StormWindow') is not None and glass_layers == 'single-pane') or \
               glass_layers == 'double-pane'

    def check_is_storm_lowe(self, window, glass_layers):
        storm_type = self.xpath(window, 'h:StormWindow/h:GlassType/text()')
        if storm_type is not None:
            return storm_type == 'low-e' and glass_layers == 'single-pane'
        return False

    duct_location_map = {'living space': 'cond_space',
                         'unconditioned space': None,
                         'under slab': None,
                         'basement': None,  # Fix me
                         'basement - unconditioned': 'uncond_basement',
                         'basement - conditioned': 'cond_space',  # Fix me
                         'crawlspace - unvented': 'unvented_crawl',
                         'crawlspace - vented': 'vented_crawl',
                         'crawlspace - unconditioned': None,  # Fix me
                         'crawlspace - conditioned': None,  # Fix me
                         'crawlspace': None,
                         'exterior wall': None,
                         'interstitial space': None,
                         'garage - conditioned': None,  # Fix me
                         'garage - unconditioned': None,  # Fix me
                         'garage': 'vented_crawl',
                         'roof deck': None,  # Fix me
                         'outside': None,
                         'attic': None,  # Fix me
                         'attic - unconditioned': 'uncond_attic',  # Fix me
                         'attic - conditioned': None,  # Fix me
                         'attic - unvented': None,  # Fix me
                         'attic - vented': None}  # Fix me
