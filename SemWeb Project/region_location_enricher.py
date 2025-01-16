import requests
import time
import logging
import re
import mwparserfromhell
from rdflib import Graph, Literal, URIRef, Namespace, OWL, XSD, BNode
from rdflib.namespace import RDF, RDFS, FOAF, DCTERMS
from urllib.parse import quote, unquote

class RegionLocationExtractor:
    def __init__(self):
        # Define namespaces
        self.base = Namespace("http://example.org/pokemon/")
        self.pokemon = Namespace("http://example.org/pokemon/")
        self.wiki = Namespace("https://bulbapedia.bulbagarden.net/wiki/")
        self.region_resource = Namespace("http://example.org/pokemon/resource/regions/")
        self.location_resource = Namespace("http://example.org/pokemon/resource/locations/")
        
        self.base_api_url = "https://bulbapedia.bulbagarden.net/w/api.php"
        self.headers = {
            'User-Agent': 'PokemonRDFEnricher/1.0 (contact@example.com)'
        }

    def get_wiki_content(self, name, is_region=True):
        """Get detailed wiki page content including wikitext, templates, images, and links"""
        params = {
            "action": "parse",
            "format": "json",
            "page": name,  # No need to append (region) or (location)
            "prop": "wikitext|templates|images|links|categories|sections|properties|externallinks|iwlinks",
            "redirects": 1,
            "disablelimitreport": 1
        }

        try:
            response = requests.get(
                self.base_api_url,
                params=params,
                headers=self.headers,
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logging.error(f"Error retrieving wiki content for {name}: {str(e)}")
            return None

    def parse_infobox(self, wikitext):
        """Parse infobox template from wikitext"""
        parsed = mwparserfromhell.parse(wikitext)
        infobox_data = {}
        
        for template in parsed.filter_templates():
            template_name = str(template.name).strip().lower()
            if 'infobox' in template_name:
                for param in template.params:
                    name = str(param.name).strip()
                    value = str(param.value).strip()
                    if value and not value.startswith('<!--'):
                        # Clean up the value
                        value = re.sub(r'<.*?>', '', value)  # Remove HTML tags
                        value = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]+)\]\]', r'\1', value)  # Extract link text
                        value = re.sub(r"'{2,}", '', value)  # Remove bold/italic markers
                        value = value.replace('\n', ' ').strip()
                        infobox_data[name] = value
        
        return infobox_data

    def extract_sections(self, parse_data):
        """Extract section content from parse data"""
        sections = {}
        if 'sections' in parse_data:
            for section in parse_data['sections']:
                section_title = section.get('line', '').strip()
                if section_title:
                    sections[section_title] = {
                        'level': section.get('level', 0),
                        'number': section.get('number', ''),
                        'index': section.get('index', '')
                    }
        return sections

    def extract_locations_from_region(self, wikitext):
        """Extract locations mentioned in a region's wiki page"""
        locations = set()
        parsed = mwparserfromhell.parse(wikitext)
        
        # Look for location links in the wikitext
        for link in parsed.filter_wikilinks():
            link_text = str(link.title)
            # Check if it's a location link
            if any(location_type in link_text.lower() for location_type in 
                  ['city', 'town', 'route', 'cave', 'forest', 'mountain', 'island', 'path', 'tower']):
                locations.add(link_text)
        
        return locations

    def create_wiki_content_graph(self, names, is_region=True):
        """Create RDF graph with detailed wiki content"""
        g = Graph()
        
        # Bind namespaces
        g.bind('base', self.base)
        g.bind('pokemon', self.pokemon)
        g.bind('wiki', self.wiki)
        g.bind('region_resource', self.region_resource)
        g.bind('location_resource', self.location_resource)
        g.bind('owl', OWL)
        g.bind('foaf', FOAF)
        g.bind('dcterms', DCTERMS)
        
        resource_type = self.pokemon.RegionResource if is_region else self.pokemon.LocationResource
        resource_ns = self.region_resource if is_region else self.location_resource
        
        for name in names:
            logging.info(f"Processing {name}...")
            
            # Create URIs
            uri_name = name.replace(' ', '_')
            resource_uri = resource_ns[quote(uri_name)]
            wiki_uri = self.wiki[quote(uri_name)]
            
            # Get wiki content
            content = self.get_wiki_content(name, is_region)
            if content and 'parse' in content:
                parse_data = content['parse']
                
                # Add basic type information
                g.add((resource_uri, RDF.type, resource_type))
                g.add((resource_uri, RDFS.label, Literal(name)))
                g.add((wiki_uri, RDF.type, self.pokemon.WikiPage))
                g.add((wiki_uri, self.pokemon.describes, resource_uri))
                
                # Add templates
                if 'templates' in parse_data:
                    for template in parse_data['templates']:
                        template_name = template.get('*', '').replace(' ', '_')
                        template_uri = URIRef(f"https://bulbapedia.bulbagarden.net/wiki/Template:{quote(template_name)}")
                        g.add((wiki_uri, self.pokemon.hasTemplate, template_uri))
                
                # Add images
                if 'images' in parse_data:
                    for image in parse_data['images']:
                        if not image.startswith('File:'):
                            continue
                        image_uri = URIRef(f"https://bulbapedia.bulbagarden.net/wiki/{quote(image)}")
                        g.add((wiki_uri, FOAF.depiction, image_uri))
                
                # Add links
                if 'links' in parse_data:
                    for link in parse_data['links']:
                        link_name = link.get('*', '').replace(' ', '_')
                        if link_name:
                            link_uri = URIRef(f"https://bulbapedia.bulbagarden.net/wiki/{quote(link_name)}")
                            g.add((wiki_uri, DCTERMS.references, link_uri))
                
                # Add external links
                if 'externallinks' in parse_data:
                    for ext_link in parse_data['externallinks']:
                        g.add((wiki_uri, RDFS.seeAlso, URIRef(ext_link)))
                
                # Add categories
                if 'categories' in parse_data:
                    for category in parse_data['categories']:
                        category_uri = URIRef(f"https://bulbapedia.bulbagarden.net/wiki/Category:{quote(str(category))}")
                        g.add((wiki_uri, DCTERMS.subject, category_uri))
                
                # Add sections
                sections = self.extract_sections(parse_data)
                for section_title, section_data in sections.items():
                    section_node = BNode()
                    g.add((wiki_uri, self.pokemon.hasSection, section_node))
                    g.add((section_node, RDFS.label, Literal(section_title)))
                    g.add((section_node, self.pokemon.sectionLevel, Literal(section_data['level'])))
                    g.add((section_node, self.pokemon.sectionNumber, Literal(section_data['number'])))
                
                # Parse infobox and wikitext
                if 'wikitext' in parse_data:
                    wikitext = parse_data['wikitext'].get('*', '')
                    infobox_data = self.parse_infobox(wikitext)
                    for key, value in infobox_data.items():
                        clean_key = re.sub(r'[^a-zA-Z0-9]', '', key)
                        if clean_key:
                            g.add((resource_uri, self.pokemon[f"infobox_{clean_key}"], Literal(value)))
                    
                    # Store the raw wikitext
                    g.add((wiki_uri, self.pokemon.wikitext, Literal(wikitext)))
                    
                    # If this is a region, extract and link its locations
                    if is_region:
                        locations = self.extract_locations_from_region(wikitext)
                        for location in locations:
                            location_uri = self.location_resource[quote(location.replace(' ', '_'))]
                            g.add((location_uri, self.pokemon.locatedInRegion, resource_uri))
            
            time.sleep(1)  # Be nice to the API
        
        return g

    def load_names(self, ttl_file):
        """Load names from TTL file"""
        g = Graph()
        g.parse(ttl_file, format='turtle')
        names = set()
        for s, p, o in g.triples((None, self.pokemon.describes, None)):
            if isinstance(o, URIRef):
                name = str(o).split('/')[-1].replace('_', ' ')
                if name:
                    names.add(name)
        return names

def extract_region_location_content():
    """Extract wiki content for regions and locations"""
    extractor = RegionLocationExtractor()
    base_path = r"C:\Users\DELL\Desktop\M2\Semantic Web Redoubl\Knowledge Graph Pokemn_AliReda12"
    
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        filename='region_location_extraction.log'
    )
    
    # Process Regions
    print("\nStarting Region wiki content extraction...")
    region_names = extractor.load_names(f"{base_path}/Here/regions_wiki_pages.ttl")
    print(f"Loaded {len(region_names)} region names")
    region_graph = extractor.create_wiki_content_graph(region_names, is_region=True)
    region_graph.serialize(f"{base_path}/Here/regions_wiki_content.ttl", format='turtle')
    print(f"Region wiki content saved to {base_path}/Here/regions_wiki_content.ttl")
    
    # Process Locations
    print("\nStarting Location wiki content extraction...")
    location_names = extractor.load_names(f"{base_path}/Here/locations_wiki_pages.ttl")
    print(f"Loaded {len(location_names)} location names")
    location_graph = extractor.create_wiki_content_graph(location_names, is_region=False)
    location_graph.serialize(f"{base_path}/Here/locations_wiki_content.ttl", format='turtle')
    print(f"Location wiki content saved to {base_path}/Here/locations_wiki_content.ttl")

if __name__ == "__main__":
    extract_region_location_content()
