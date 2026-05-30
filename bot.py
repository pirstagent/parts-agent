async def create_ebay_draft(listing):
    if not EBAY_USER_TOKEN:
        return "NO_USER_TOKEN"

    title = listing.get("title", "Auto Part")[:80]
    description = listing.get("description", "No description")
    price = listing.get("suggested_price", 25)
    
    brand = listing.get("brand", "Unbranded")
    if brand.upper() == "OEM" or not brand:
        brand = "Unbranded"
        
    part_num = listing.get("part_number", listing.get("oem_number", "Universal"))

    item_specifics = (
        "<ItemSpecifics>"
        "<NameValueList><Name>Brand</Name><Value>" + brand + "</Value></NameValueList>"
        "<NameValueList><Name>Manufacturer Part Number</Name><Value>" + part_num + "</Value></NameValueList>"
        "<NameValueList><Name>Type</Name><Value>Direct Replacement</Value></NameValueList>"
        "<NameValueList><Name>Grade</Name><Value>A</Value></NameValueList>"
        "<NameValueList><Name>Quality</Name><Value>Excellent</Value></NameValueList>"
        "<NameValueList><Name>Certification</Name><Value>OEM Genuine</Value></NameValueList>"
        "<NameValueList><Name>OE/OEM Part Number</Name><Value>" + part_num + "</Value></NameValueList>"
        "</ItemSpecifics>"
    )

    picture_details = (
        "<PictureDetails>"
        "<PictureURL>https://upload.wikimedia.org/wikipedia/commons/thumb/e/e0/Car_with_Driver-Side_A-Pillar_Highlighted.jpg/800px-Car_with_Driver-Side_A-Pillar_Highlighted.jpg</PictureURL>"
        "</PictureDetails>"
    )

    # შეცვლილია AddFixedPriceItemRequest -> AddItemRequest და დამატებულია ის ველები, რაც eBay-ს აიძულებს ნივთი მხოლოდ დრაფტად შეინახოს
    xml_request = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<AddItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
        "<RequesterCredentials>"
        "<eBayAuthToken>" + EBAY_USER_TOKEN + "</eBayAuthToken>"
        "</RequesterCredentials>"
        "<ErrorLanguage>en_US</ErrorLanguage>"
        "<WarningLevel>High</WarningLevel>"
        "<Item>"
        "<Title>" + title + "</Title>"
        "<Description><![CDATA[" + description + "]]></Description>"
        "<PrimaryCategory><CategoryID>262</CategoryID></PrimaryCategory>"
        "<StartPrice>" + str(price) + "</StartPrice>"
        "<CategoryMappingAllowed>true</CategoryMappingAllowed>"
        + item_specifics + picture_details +
        "<Country>US</Country>"
        "<Currency>USD</Currency>"
        "<DispatchTimeMax>3</DispatchTimeMax>"
        "<ListingDuration>GTC</ListingDuration>"
        "<ListingType>FixedPriceItem</ListingType>"
        "<PaymentMethods>PayPal</PaymentMethods>"
        "<PayPalEmailAddress>" + PAYPAL_EMAIL + "</PayPalEmailAddress>"
        "<PostalCode>10965</PostalCode>"
        "<Quantity>1</Quantity>"
        "<ReturnPolicy>"
        "<ReturnsAcceptedOption>ReturnsAccepted</ReturnsAcceptedOption>"
        "<RefundOption>MoneyBack</RefundOption>"
        "<ReturnsWithinOption>Days_30</ReturnsWithinOption>"
        "<ShippingCostPaidByOption>Buyer</ShippingCostPaidByOption>"
        "</ReturnPolicy>"
        "<ShippingDetails>"
        "<ShippingType>Flat</ShippingType>"
        "<ShippingServiceOptions>"
        "<ShippingServicePriority>1</ShippingServicePriority>"
        "<ShippingService>USPSPriority</ShippingService>"
        "<ShippingServiceCost>9.99</ShippingServiceCost>"
        "</ShippingServiceOptions>"
        "</ShippingDetails>"
        "<ShipToLocations>US</ShipToLocations>"
        "<Site>US</Site>"
        "</Item>"
        # ეს ფლეგი ეუბნება eBay-ს, რომ ნივთი მხოლოდ დრაფტებში ჩააგდოს!
        "<IsDraft>true</IsDraft>" 
        '</AddItemRequest>'
    )

    headers = {
        "X-EBAY-API-SITEID": "0",
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-CALL-NAME": "AddItem", # შეცვლილია AddItem-ზე
        "X-EBAY-API-APP-NAME": EBAY_APP_ID or "",
        "X-EBAY-API-DEV-NAME": EBAY_DEV_ID or "",
        "X-EBAY-API-CERT-NAME": EBAY_CERT_ID or "",
        "Content-Type": "text/xml"
    }

    try:
        response = requests.post(
            "https://api.ebay.com/ws/api.dll",
            headers=headers,
            data=xml_request.encode("utf-8"),
            timeout=30
        )
        logger.info("eBay response: " + response.text[:600])
        
        # თუ დრაფტი წარმატებით შეიქმნა, eBay აბრუნებს მის უნიკალურ ID-ს
        match = re.search(r"<ItemID>(\d+)</ItemID>", response.text)
        if match:
            return match.group(1)
            
        errors = re.findall(r"<ShortMessage>(.*?)</ShortMessage>", response.text)
        if errors:
            return "eBay პასუხი: " + " | ".join(errors[:3])
        return "მომზადებულია"
    except Exception as e:
        logger.error("eBay error: " + str(e))
        return "LOCAL_DRAFT"
